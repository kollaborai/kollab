"""Risk assessment for tool operations."""

import logging
from typing import Any, Dict, Optional

from kollabor_events.permissions_models import (
    RiskAssessmentResult,
    RiskAssessmentRules,
    ToolRiskLevel,
)

logger = logging.getLogger(__name__)


class RiskAssessor:
    """Assesses risk level for tool operations."""

    def __init__(self, rules: RiskAssessmentRules, config: dict):
        """Initialize risk assessor.

        Args:
            rules: Risk assessment rules
            config: Application configuration
        """
        self._rules = rules
        self._config = config

    def assess_tool(self, tool_data: Dict[str, Any]) -> RiskAssessmentResult:
        """
        Assess the risk level of a tool operation.

        Args:
            tool_data: Tool information from response parser
                - type: str (terminal, mcp, file_write, etc.)
                - name: str (tool name)
                - command: str (for terminal commands)
                - arguments: dict (tool arguments)

        Returns:
            RiskAssessmentResult with level and reasoning
        """
        tool_type = tool_data.get("type", "unknown")
        tool_name = tool_data.get("name", "")
        command = tool_data.get("command", "")

        # Check if tool is blocked
        if tool_name in self._rules.blocked_tools:
            return RiskAssessmentResult(
                level=ToolRiskLevel.HIGH,
                reason=f"Tool '{tool_name}' is blocked by configuration",
                is_blocked=True,
            )

        # Check if tool is trusted
        if tool_name in self._rules.trusted_tools:
            return RiskAssessmentResult(
                level=ToolRiskLevel.LOW,
                reason=f"Tool '{tool_name}' is in trusted list",
                tool_type=tool_type,
            )

        # For terminal commands, check patterns
        if tool_type == "terminal" and command:
            result = self._assess_command(command)
            if result:
                return result

        # Fall back to tool type default
        default_level = self._rules.tool_type_risks.get(
            tool_type, ToolRiskLevel.UNKNOWN
        )

        return RiskAssessmentResult(
            level=default_level,
            reason=f"Default risk for tool type '{tool_type}'",
            tool_type=tool_type,
        )

    def _assess_command(self, command: str) -> Optional[RiskAssessmentResult]:
        """Assess risk of a shell command.

        Args:
            command: Shell command to assess

        Returns:
            RiskAssessmentResult if pattern matches, None otherwise
        """
        # Check HIGH risk patterns
        for pattern in self._rules.high_risk_patterns:
            if pattern.search(command):
                return RiskAssessmentResult(
                    level=ToolRiskLevel.HIGH,
                    reason="Command matches high-risk pattern",
                    matched_pattern=pattern.pattern,
                    tool_type="terminal",
                    requires_confirmation=True,
                )

        # Check MEDIUM risk patterns
        for pattern in self._rules.medium_risk_patterns:
            if pattern.search(command):
                return RiskAssessmentResult(
                    level=ToolRiskLevel.MEDIUM,
                    reason="Command matches medium-risk pattern",
                    matched_pattern=pattern.pattern,
                    tool_type="terminal",
                )

        return None
