"""set_approval_mode(persist=False) changes the session, not the saved default (#1111)."""

import asyncio
import json

from kollabor.state.local import LocalStateService
from kollabor_agent.permissions import PermissionManager, RiskAssessor
from kollabor_events.permissions_models import ApprovalMode, RiskAssessmentRules


def _make_state(tmp_path):
    from kollabor_config.service import ConfigService

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"kollabor": {"permissions": {"approval_mode": "default"}}})
    )
    config_service = ConfigService(config_path)
    manager = PermissionManager(
        config={"kollabor": {"permissions": {"enabled": True}}},
        risk_assessor=RiskAssessor(RiskAssessmentRules(), {"kollabor": {}}),
        event_bus=None,
        config_service=config_service,
    )
    return config_path, LocalStateService(
        llm_service=None, profile_manager=None, permission_manager=manager
    )


def _saved_mode(config_path):
    return json.loads(config_path.read_text())["kollabor"]["permissions"][
        "approval_mode"
    ]


def test_persist_false_changes_session_but_not_saved_default(tmp_path):
    config_path, state = _make_state(tmp_path)

    snapshot = asyncio.run(state.set_approval_mode("trust_all", persist=False))

    assert snapshot.approval_mode == "TRUST_ALL"
    assert state._permission_manager.approval_mode is ApprovalMode.TRUST_ALL
    assert _saved_mode(config_path) == "default"


def test_persist_true_still_saves_the_default(tmp_path):
    config_path, state = _make_state(tmp_path)

    asyncio.run(state.set_approval_mode("trust_all", persist=True))

    assert _saved_mode(config_path) == "trust_all"


class _InProcessRpc:
    """Routes RemoteStateService calls to the daemon's registered handlers."""

    def __init__(self):
        self.handlers = {}

    def register(self, method, handler):
        self.handlers[method] = handler

    async def call(self, method, params, timeout=None):
        return await self.handlers[method](params)


def test_engine_rpc_path_keeps_the_saved_default(tmp_path):
    """The engine reaches the daemon through RemoteStateService and the RPC handler."""
    from kollabor.state.handlers import register_state_handlers
    from kollabor.state.remote import RemoteStateService

    config_path, local = _make_state(tmp_path)
    rpc = _InProcessRpc()
    register_state_handlers(rpc, local)
    remote = RemoteStateService(rpc)

    snapshot = asyncio.run(remote.set_approval_mode("trust_all", persist=False))

    assert snapshot.approval_mode == "TRUST_ALL"
    assert local._permission_manager.approval_mode is ApprovalMode.TRUST_ALL
    assert _saved_mode(config_path) == "default"
