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


def test_acp_grants_are_namespaced_and_scope_exact(tmp_path, monkeypatch):
    config = {"kollabor": {"permissions": {"enabled": True}}}
    manager = PermissionManager(
        config=config,
        risk_assessor=RiskAssessor(RiskAssessmentRules(), config),
        event_bus=_EventBus(),
    )
    operation = {"kind": "execute", "command": ["echo", "ok"]}
    key = manager.grant_acp(operation, str(tmp_path), "claude-code")
    assert key.startswith("acp:")
    assert manager.has_acp_grant(operation, str(tmp_path), "claude-code")
    assert not manager.has_acp_grant(operation, str(tmp_path / "other"), "claude-code")
    assert not manager.has_acp_grant(operation, str(tmp_path), "codex-cli")
    assert manager.get_project_approvals() == []
    manager.clear_acp_grants()
    assert not manager.has_acp_grant(operation, str(tmp_path), "claude-code")


def test_acp_project_grant_persists_without_native_keys(tmp_path, monkeypatch):
    from kollabor_config import config_utils

    monkeypatch.setattr(config_utils, "get_project_data_dir", lambda: tmp_path)
    config = {"kollabor": {"permissions": {"enabled": True}}}
    manager = PermissionManager(
        config=config,
        risk_assessor=RiskAssessor(RiskAssessmentRules(), config),
        event_bus=_EventBus(),
    )
    operation = {"kind": "edit", "locations": ["/tmp/work.txt"]}
    manager.grant_acp(operation, str(tmp_path), "claude-code", scope="project")
    data = __import__("json").loads((tmp_path / "permission_approvals.json").read_text())
    assert data["approved_keys"] == []
    assert len(data["acp_grants"]) == 1
    restored = PermissionManager(
        config=config,
        risk_assessor=RiskAssessor(RiskAssessmentRules(), config),
        event_bus=_EventBus(),
    )
    assert restored.has_acp_grant(operation, str(tmp_path), "claude-code")
