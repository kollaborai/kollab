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
from typing import Any, Dict, List

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


def _split_evidence(body: str) -> List[Dict[str, str]]:
    """Parse the evidence body into {ref, claim} dicts.

    A ref is ``tool_type:output`` where output can be multi-line (for
    example a git log). Lines are wrapped-ref continuations until a
    ``::`` separator is found: everything before the FIRST ``::`` on the
    physical line joins the ref, everything after is the claim.
    """
    evidence: List[Dict[str, str]] = []
    ref_buffer: List[str] = []
    claim_buffer: List[str] = []
    in_claim = False

    def flush() -> None:
        if ref_buffer:
            ref = "\n".join(ref_buffer).strip()
            claim = "\n".join(claim_buffer).strip()
            if ref:
                evidence.append({"ref": ref, "claim": claim})
        ref_buffer.clear()
        claim_buffer.clear()

    for raw in (body or "").strip().splitlines():
        line = raw.strip()
        if not line:
            continue
        m = EVIDENCE_LINE.match(line)
        if m and not in_claim:
            ref_buffer.append(m.group(1))
            claim_buffer.append(m.group(2))
            in_claim = True
        elif m and in_claim:
            flush()
            ref_buffer.append(m.group(1))
            claim_buffer.append(m.group(2))
        elif in_claim:
            claim_buffer.append(line)
        else:
            ref_buffer.append(line)
    flush()
    return evidence


def _extract_goal_report(match: re.Match) -> Dict[str, Any]:
    kind = match.group(1).lower()
    version = int(match.group(2)) if match.group(2) else None
    reason = match.group(3) or ""
    evidence = _split_evidence(match.group(4) or "")
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
            reason = (
                "no goal turn is in flight; goal_report only runs "
                "inside a goal-owned turn (goal may be paused, or the "
                "turn already settled — resume it and report inside the "
                "resumed turn)"
            )
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="goal_report",
                success=False,
                output=reason,
                error=reason,
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
        error = "" if result.get("accepted") else str(result.get("detail", ""))[:300]
        return ToolExecutionResult(
            tool_id=tool_id,
            tool_type="goal_report",
            success=bool(result.get("accepted")),
            output=detail,
            error=error,
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
