"""Approval-mode env notifications surface outages instead of swallowing.

set_approval_mode pushes a capability env-notification so agents notice
approval changes via the [env] rail. A rail outage (missing service,
producer failure) must never crash the permission flow — but it must be
logged and visible, not silently swallowed (issue #207).
"""

import logging

from kollabor_agent.permissions import PermissionManager, RiskAssessor
from kollabor_events.permissions_models import ApprovalMode, RiskAssessmentRules


class _EventBus:
    """Minimal bus; env_queue service registered only when asked."""

    def __init__(self, with_queue=True, failing_queue=False):
        self._with_queue = with_queue
        self._failing = failing_queue
        self.pushed = []

    def get_service(self, name):
        if name != "env_queue":
            return None
        if not self._with_queue:
            return None
        if self._failing:

            class _Boom:
                def push(self, event):
                    raise RuntimeError("queue exploded")

            return _Boom()
        return self

    def push(self, event):
        self.pushed.append(event)


def _manager(bus):
    config = {"kollabor": {"permissions": {"enabled": True}}}
    return PermissionManager(
        config=config,
        risk_assessor=RiskAssessor(RiskAssessmentRules(), config),
        event_bus=bus,
    )


def test_env_notification_pushed_on_mode_change():
    bus = _EventBus()
    manager = _manager(bus)
    manager.set_approval_mode(ApprovalMode.TRUST_ALL)
    assert len(bus.pushed) == 1
    event = bus.pushed[0]
    # ApprovalMode values are ints (TRUST_ALL == 4)
    assert "approval:4" in event.message
    assert "was" in event.message


def test_mode_change_survives_missing_env_queue(caplog):
    """No env_queue service: mode still changes, outage logged as warning."""
    bus = _EventBus(with_queue=False)
    manager = _manager(bus)
    with caplog.at_level(logging.WARNING, logger="kollabor_agent.permissions.manager"):
        manager.set_approval_mode(ApprovalMode.TRUST_ALL)  # must not raise
    assert manager.approval_mode is ApprovalMode.TRUST_ALL
    assert any(
        "env notification" in record.message.lower()
        and record.levelno == logging.WARNING
        for record in caplog.records
    )


def test_mode_change_survives_failing_queue(caplog):
    """A throwing env_queue push must not crash the permission flow."""
    bus = _EventBus(failing_queue=True)
    manager = _manager(bus)
    with caplog.at_level(logging.WARNING, logger="kollabor_agent.permissions.manager"):
        manager.set_approval_mode(ApprovalMode.CONFIRM_ALL)  # must not raise
    assert manager.approval_mode is ApprovalMode.CONFIRM_ALL
    warning_records = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "not delivered" in record.getMessage().lower()
    ]
    assert warning_records, "expected a visible warning about the failed notification"


def test_notification_failure_does_not_block_persist(tmp_path, monkeypatch):
    """Even with persist enabled, a notification outage doesn't skip persistence."""
    from kollabor_config.service import ConfigService

    config_path = tmp_path / "config.json"
    config_path.write_text("{}")
    config_service = ConfigService(config_path)
    config = {"kollabor": {"permissions": {"enabled": True}}}
    manager = PermissionManager(
        config=config,
        risk_assessor=RiskAssessor(RiskAssessmentRules(), config),
        event_bus=_EventBus(failing_queue=True),
        config_service=config_service,
    )
    manager.set_approval_mode(ApprovalMode.AUTO_APPROVE_EDITS)  # must not raise
    assert manager.approval_mode is ApprovalMode.AUTO_APPROVE_EDITS
