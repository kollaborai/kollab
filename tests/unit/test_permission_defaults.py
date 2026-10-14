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
    inventory = restored.get_acp_grant_inventory()
    assert inventory == [{
        "grant_key": restored.acp_grant_key(operation, str(tmp_path), "claude-code"),
        "operation": operation,
        "workspace": str(tmp_path.resolve()),
        "profile": "claude-code",
        "scope": "project",
        "source": "kollab",
    }]
    restored.clear_project_approvals()
    assert restored.get_acp_grant_inventory() == []


def test_acp_inventory_scope_reload_clear_and_isolation(tmp_path, monkeypatch):
    from kollabor_config import config_utils
    import json

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    monkeypatch.setattr(config_utils, "get_project_data_dir", lambda: project_dir)
    config = {"kollabor": {"permissions": {"enabled": True}}}
    operation = {"kind": "execute", "command": ["echo", "ok"]}
    manager = PermissionManager(config=config, risk_assessor=RiskAssessor(RiskAssessmentRules(), config), event_bus=_EventBus())
    manager.grant_acp(operation, str(tmp_path), "profile-a", scope="session")
    manager.grant_acp(operation, str(tmp_path), "profile-a", scope="project")
    assert len(manager.get_acp_grant_inventory()) == 2
    assert manager.has_acp_grant(operation, str(tmp_path), "profile-a")
    assert not manager.has_acp_grant(operation, str(tmp_path), "profile-b")
    assert not manager.has_acp_grant({"kind": "execute", "command": ["echo", "no"]}, str(tmp_path), "profile-a")

    restored = PermissionManager(config=config, risk_assessor=RiskAssessor(RiskAssessmentRules(), config), event_bus=_EventBus())
    scopes = {row["scope"] for row in restored.get_acp_grant_inventory()}
    assert scopes == {"project"}
    restored.clear_session_approvals()
    assert {row["scope"] for row in restored.get_acp_grant_inventory()} == {"project"}
    restored.clear_project_approvals()
    assert restored.get_acp_grant_inventory() == []


def test_legacy_and_vendor_records_are_not_invented_as_revokeable_grants(tmp_path, monkeypatch):
    from kollabor_config import config_utils
    import json

    monkeypatch.setattr(config_utils, "get_project_data_dir", lambda: tmp_path)
    config = {"kollabor": {"permissions": {"enabled": True}}}
    operation = {"kind": "edit", "locations": ["/tmp/a"]}
    manager = PermissionManager(config=config, risk_assessor=RiskAssessor(RiskAssessmentRules(), config), event_bus=_EventBus())
    key = manager.acp_grant_key(operation, str(tmp_path), "profile-a")
    (tmp_path / "permission_approvals.json").write_text(json.dumps({
        "approved_keys": [], "acp_grants": [key],
        "vendor_saved_choices": [{"profile": "vendor", "operation": operation}],
    }))
    restored = PermissionManager(config=config, risk_assessor=RiskAssessor(RiskAssessmentRules(), config), event_bus=_EventBus())
    rows = restored.get_acp_grant_inventory()
    assert len(rows) == 1
    assert rows[0]["grant_key"] == key
    assert rows[0]["operation"] is None
    assert rows[0]["profile"] is None
    assert all(row.get("source") == "kollab" for row in rows)


def test_acp_clear_preserves_native_approval_keys_and_metadata_tamper_is_not_auth(tmp_path, monkeypatch):
    from kollabor_config import config_utils
    import json

    monkeypatch.setattr(config_utils, "get_project_data_dir", lambda: tmp_path)
    config = {"kollabor": {"permissions": {"enabled": True}}}
    operation = {"kind": "execute", "command": ["python", "-c", "print(1)"]}
    manager = PermissionManager(config=config, risk_assessor=RiskAssessor(RiskAssessmentRules(), config), event_bus=_EventBus())
    native_key = "execute:python -c print(1)"
    manager._project_approvals.add(native_key)
    key = manager.grant_acp(operation, str(tmp_path), "profile-a", scope="project")
    manager.clear_acp_grants(scope="project")
    assert manager.get_project_approvals() == [native_key]
    assert not manager.has_acp_grant(operation, str(tmp_path), "profile-a")

    manager.grant_acp(operation, str(tmp_path), "profile-a", scope="project")
    data = json.loads((tmp_path / "permission_approvals.json").read_text())
    data["acp_grant_metadata"][key]["operation"] = {"kind": "execute", "command": ["evil"]}
    (tmp_path / "permission_approvals.json").write_text(json.dumps(data))
    restored = PermissionManager(config=config, risk_assessor=RiskAssessor(RiskAssessmentRules(), config), event_bus=_EventBus())
    assert restored.has_acp_grant(operation, str(tmp_path), "profile-a")
    assert not restored.has_acp_grant(data["acp_grant_metadata"][key]["operation"], str(tmp_path), "profile-a")


def test_vendor_saved_choices_are_not_inventory_producer(tmp_path, monkeypatch):
    from kollabor_config import config_utils
    import json

    monkeypatch.setattr(config_utils, "get_project_data_dir", lambda: tmp_path)
    config = {"kollabor": {"permissions": {"enabled": True}}}
    vendor_operation = {"kind": "execute", "command": ["vendor-tool"]}
    (tmp_path / "permission_approvals.json").write_text(json.dumps({
        "approved_keys": [], "acp_grants": [],
        "vendor_saved_choices": [{"profile": "vendor", "operation": vendor_operation}],
    }))
    manager = PermissionManager(config=config, risk_assessor=RiskAssessor(RiskAssessmentRules(), config), event_bus=_EventBus())
    assert manager.get_acp_grant_inventory() == []
    assert not manager.has_acp_grant(vendor_operation, str(tmp_path), "vendor")


def test_malformed_and_forged_metadata_never_changes_authorization_or_identity(tmp_path, monkeypatch):
    from kollabor_config import config_utils
    import json

    monkeypatch.setattr(config_utils, "get_project_data_dir", lambda: tmp_path)
    config = {"kollabor": {"permissions": {"enabled": True}}}
    operation = {"kind": "execute", "command": ["echo", "safe"]}
    manager = PermissionManager(config=config, risk_assessor=RiskAssessor(RiskAssessmentRules(), config), event_bus=_EventBus())
    key = manager.grant_acp(operation, str(tmp_path), "profile-a", scope="project")
    data = json.loads((tmp_path / "permission_approvals.json").read_text())
    data["approved_keys"].append("native:keep")
    data["acp_grant_metadata"] = ["garbage"]
    (tmp_path / "permission_approvals.json").write_text(json.dumps(data))
    restored = PermissionManager(config=config, risk_assessor=RiskAssessor(RiskAssessmentRules(), config), event_bus=_EventBus())
    assert restored.has_acp_grant(operation, str(tmp_path), "profile-a")
    assert restored.get_project_approvals() == ["native:keep"]
    assert restored.get_acp_grant_inventory()[0]["operation"] is None
    data["acp_grant_metadata"] = {key: "not-a-record", "garbage": 42}
    (tmp_path / "permission_approvals.json").write_text(json.dumps(data))
    non_dict = PermissionManager(config=config, risk_assessor=RiskAssessor(RiskAssessmentRules(), config), event_bus=_EventBus())
    assert non_dict.get_acp_grant_inventory()[0]["operation"] is None

    data["acp_grant_metadata"] = {key: {
        "grant_key": "acp:forged", "scope": "session", "source": "vendor",
        "operation": operation, "workspace": str(tmp_path), "profile": "profile-a",
    }}
    (tmp_path / "permission_approvals.json").write_text(json.dumps(data))
    forged = PermissionManager(config=config, risk_assessor=RiskAssessor(RiskAssessmentRules(), config), event_bus=_EventBus())
    row = forged.get_acp_grant_inventory()[0]
    assert row["grant_key"] == key
    assert row["scope"] == "project"
    assert row["source"] == "kollab"
    assert row["operation"] == operation

    data["acp_grant_metadata"][key]["profile"] = "other-profile"
    (tmp_path / "permission_approvals.json").write_text(json.dumps(data))
    mismatched = PermissionManager(config=config, risk_assessor=RiskAssessor(RiskAssessmentRules(), config), event_bus=_EventBus())
    assert mismatched.get_acp_grant_inventory()[0]["operation"] is None
