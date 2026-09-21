"""goal_report tool: the single reporting surface for goal controls (9.1).

Registered through the unified tool pipeline (register_plugin_tag +
register_plugin_handler for XML-tool providers; the same handler serves
native tool registration). Hooks, permissions, and approvals apply to goal
reports exactly like any other tool because they run in the real pipeline.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict

logger = logging.getLogger(__name__)

GOAL_REPORT_PATTERN = re.compile(
    r'<goal_report\s+kind="(progress|complete|blocked)"'
    r'(?:\s+version="(\d+)")?'
    r'(?:\s+reason="([^"]*)")?\s*?>'
    r"(.*?)</goal_report>",
    re.DOTALL | re.IGNORECASE,
)

#: evidence body is one `ref :: claim` per line (model-friendly)
EVIDENCE_LINE = re.compile(r"^\s*(.+?)\s*::\s*(.+?)\s*$")


def _extract_goal_report(match: re.Match) -> Dict[str, Any]:
    kind = match.group(1).lower()
    version = int(match.group(2)) if match.group(2) else None
    reason = match.group(3) or ""
    evidence = []
    for line in (match.group(4) or "").strip().splitlines():
        line = line.strip()
        if not line:
            continue
        m = EVIDENCE_LINE.match(line)
        if m:
            evidence.append({"ref": m.group(1), "claim": m.group(2)})
    return {
        "kind": kind,
        "expected_record_version": version,
        "reason": reason,
        "evidence": evidence,
    }


def make_goal_report_handler(service):
    """Build the async tool handler bound to a GoalService."""

    async def _handle_goal_report(tool_data: Dict[str, Any]):
        from kollabor_agent.tool_executor import ToolExecutionResult

        tool_id = tool_data.get("id", "unknown")
        current = service.current_goal_attempt()
        if current is None:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="goal_report",
                success=False,
                output=(
                    "no goal turn is in flight; goal_report only runs "
                    "inside a goal-owned turn"
                ),
            )
        record, attempt = current
        version = tool_data.get("expected_record_version")
        if version is None:
            version = record.record_version
        from kollabor.goals.service import GoalControl

        control = GoalControl(
            goal_id=record.goal_id,
            expected_record_version=int(version),
            kind=tool_data.get("kind", "progress"),
            reason=(tool_data.get("reason") or "")[:600],
            evidence=tool_data.get("evidence") or [],
        )
        result = service.handle_goal_report(control, attempt)
        detail = json.dumps(result, default=str)
        return ToolExecutionResult(
            tool_id=tool_id,
            tool_type="goal_report",
            success=bool(result.get("accepted")),
            output=detail,
        )

    return _handle_goal_report


def register_goal_tools(response_parser, tool_executor, service) -> None:
    """Wire the goal_report tag + handler into the unified tool pipeline."""
    response_parser.register_plugin_tag(
        "goal_report", GOAL_REPORT_PATTERN, "goal_report", _extract_goal_report
    )
    tool_executor.register_plugin_handler(
        "goal_report", make_goal_report_handler(service)
    )
    logger.info("goal_report tool registered through unified pipeline")
