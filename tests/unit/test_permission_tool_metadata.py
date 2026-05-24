"""Permission tests for registered tool metadata and hook policy."""

import asyncio
from types import SimpleNamespace

from kollabor_engine.session import EngineSession

from kollabor.llm.permissions.hook import PermissionHook
from kollabor_agent.permissions.risk_assessor import RiskAssessor
from kollabor_events.dict_utils import deep_merge, safe_get
from kollabor_events.permissions_config import PERMISSION_CONFIG_DEFAULTS
from kollabor_events.permissions_models import (
    ApprovalMode,
    RiskAssessmentRules,
    ToolRiskLevel,
)


class CapturingEventBus:
    """Tiny event bus double that records registered hooks."""

    def __init__(self):
        self.hooks = []

    async def register_hook(self, hook):
        self.hooks.append(hook)
        return True

    async def emit_with_hooks(self, *args, **kwargs):
        return {}


def test_permission_defaults_are_nested_and_start_in_default_mode():
    config = deep_merge(PERMISSION_CONFIG_DEFAULTS, {})
    assessor = RiskAssessor(rules=RiskAssessmentRules(), config=config)

    from kollabor_agent.permissions.manager import PermissionManager

    manager = PermissionManager(
        config=config,
        risk_assessor=assessor,
        event_bus=CapturingEventBus(),
    )

    assert config.get("kollabor", {}).get("permissions", {}).get("enabled") is True
    assert config.get("kollabor", {}).get("permissions", {}).get("approval_mode") == "default"
    assert manager.approval_mode is ApprovalMode.DEFAULT

    decision = asyncio.run(
        manager.check_permission({"id": "tool-1", "type": "hub_msg", "name": "hub_msg"})
    )

    assert decision.allowed is True
    assert decision.risk_level is ToolRiskLevel.LOW


def test_registry_metadata_marks_collaboration_tools_low_risk():
    assessor = RiskAssessor(rules=RiskAssessmentRules(), config={})

    for tool_name in ("hub_msg", "state_update", "scratchpad"):
        result = assessor.assess_tool({"type": tool_name, "name": tool_name})

        assert result.level is ToolRiskLevel.LOW
        assert result.requires_confirmation is False


def test_permission_hook_fails_closed_without_executor_retries():
    event_bus = CapturingEventBus()

    asyncio.run(PermissionHook(permission_manager=object()).register(event_bus))

    assert len(event_bus.hooks) == 1
    hook = event_bus.hooks[0]
    assert hook.plugin_name == "permission_system"
    assert hook.name == "permission_check"
    assert hook.timeout == 300
    assert hook.retry_attempts == 0
    assert hook.error_action == "stop"


def test_engine_permission_hook_fails_closed_without_executor_retries():
    event_bus = CapturingEventBus()
    session = SimpleNamespace(
        permission_manager=object(),
        event_bus=event_bus,
        session_id="test-session",
    )

    asyncio.run(EngineSession._register_permission_hook(session))

    assert len(event_bus.hooks) == 1
    hook = event_bus.hooks[0]
    assert hook.plugin_name == "engine_permission_system"
    assert hook.name == "permission_check"
    assert hook.timeout == 300
    assert hook.retry_attempts == 0
    assert hook.error_action == "stop"
