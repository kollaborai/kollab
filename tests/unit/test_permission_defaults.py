"""Tests for implicit permission-mode defaults."""

from kollabor_agent.permissions import PermissionManager, RiskAssessor
from kollabor_events.permissions_models import ApprovalMode, RiskAssessmentRules


class _EventBus:
    def get_service(self, _name):
        return None


def test_missing_permission_mode_falls_back_to_trust_all():
    config = {"kollabor": {"permissions": {"enabled": True}}}
    manager = PermissionManager(
        config=config,
        risk_assessor=RiskAssessor(RiskAssessmentRules(), config),
        event_bus=_EventBus(),
    )

    assert manager.approval_mode is ApprovalMode.TRUST_ALL
