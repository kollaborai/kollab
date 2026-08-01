"""Permission tests for registered tool metadata and hook policy."""

import asyncio

from kollabor_engine.session import (
    _confirmation_response_name,
    _permission_input_payload,
)

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

    assert safe_get(config, "kollabor.permissions.enabled") is True
    assert safe_get(config, "kollabor.permissions.approval_mode") == "default"
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


def test_terminal_commands_use_command_risk_before_tool_definition():
    assessor = RiskAssessor(rules=RiskAssessmentRules(), config={})

    safe = assessor.assess_tool(
        {"id": "tool-1", "type": "terminal", "name": "terminal", "command": "ls -la"}
    )
    dangerous = assessor.assess_tool(
        {"id": "tool-2", "type": "terminal", "name": "terminal", "command": "rm -rf /"}
    )

    assert safe.level is ToolRiskLevel.MEDIUM
    assert safe.reason == "Default risk for tool type 'terminal'"
    assert dangerous.level is ToolRiskLevel.HIGH


def test_permission_input_payload_includes_terminal_command():
    payload = _permission_input_payload(
        {
            "id": "tool-1",
            "type": "terminal",
            "name": "terminal",
            "command": "cat package.json",
            "cwd": "/repo",
        }
    )

    assert payload == {"command": "cat package.json", "cwd": "/repo"}


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


def test_engine_permission_decisions_fail_closed():
    """The engine no longer registers its own permission hook - the session's
    daemon owns permissions, and that hook is covered above. What the engine
    still owns is the translation from the HTTP decision+scope onto the
    daemon's ConfirmationResponse, and that must never widen access."""
    assert _confirmation_response_name("approve", "once") == "APPROVE_ONCE"
    assert _confirmation_response_name("approve", "session") == "APPROVE_SESSION"
    assert _confirmation_response_name("approve", "project") == "APPROVE_PROJECT"
    assert _confirmation_response_name("approve", "trust_tool") == "APPROVE_TOOL_ALWAYS"

    # Anything not explicitly an approval denies
    assert _confirmation_response_name("deny", "once") == "DENY"
    assert _confirmation_response_name("", "project") == "DENY"
    assert _confirmation_response_name("APPROVE", "once") == "DENY"
    assert _confirmation_response_name("yes", "trust_tool") == "DENY"

    # An unrecognized scope narrows to a single-use approval, never widens
    assert _confirmation_response_name("approve", "forever") == "APPROVE_ONCE"
    assert _confirmation_response_name("approve", "") == "APPROVE_ONCE"


def test_engine_confirmation_names_exist_on_the_enum():
    """A typo here would silently deny every prompt, since the daemon's parser
    falls back to DENY for names it doesn't recognize."""
    from kollabor_engine.session import _APPROVE_SCOPE_RESPONSES

    from kollabor_events.permissions_models import ConfirmationResponse

    for name in list(_APPROVE_SCOPE_RESPONSES.values()) + ["DENY"]:
        assert name in ConfirmationResponse.__members__, name
