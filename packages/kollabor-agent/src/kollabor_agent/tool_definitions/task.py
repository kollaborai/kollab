"""Task lifecycle tool definitions.

Tools for reporting progress on tasks within a session.
"""

from ..tool_definition import ToolDefinition, ToolParameter
from ..tool_registry import get_registry

# --- task-checkpoint ---
task_checkpoint = ToolDefinition(
    name="task-checkpoint",
    description="Report progress update on a task.",
    category="task",
    risk_level="low",
    requires_permission=False,
    xml_tag="task_checkpoint",
    xml_form="mixed",
    xml_attributes=["id"],
    parameters=[
        ToolParameter(
            name="task_id",
            type="string",
            description="Task identifier",
            required=True,
        ),
        ToolParameter(
            name="progress",
            type="string",
            description="Description of progress made",
            required=True,
        ),
    ],
    examples=[
        '<task_checkpoint id="phase-b">migrated scratchpad and wait tools</task_checkpoint>',
    ],
    result_format="Progress update acknowledged.",
)

# --- task-complete ---
task_complete = ToolDefinition(
    name="task-complete",
    description="Mark a task as done.",
    category="task",
    risk_level="low",
    requires_permission=False,
    xml_tag="task_complete",
    xml_form="mixed",
    xml_attributes=["id"],
    xml_body_param="summary",
    parameters=[
        ToolParameter(
            name="task_id",
            type="string",
            description="Task identifier",
            required=True,
        ),
        ToolParameter(
            name="summary",
            type="string",
            description="Summary of what was completed",
            required=True,
        ),
    ],
    examples=[
        '<task_complete id="phase-b">phase B migration shipped — 28 tools registered</task_complete>',
    ],
    result_format="Task marked as complete.",
    key_rules=[
        "task tags are for reporting, not for creating new tasks",
        "after task_complete, stop naturally when no more tool calls are needed",
    ],
)

# --- task-approve ---
task_approve = ToolDefinition(
    name="task-approve",
    description="Approve a task (used by reviewing agents).",
    category="task",
    risk_level="low",
    requires_permission=False,
    xml_tag="task_approve",
    xml_form="mixed",
    xml_attributes=["id"],
    xml_body_param="notes",
    parameters=[
        ToolParameter(
            name="task_id",
            type="string",
            description="ID of the task to approve",
            required=True,
        ),
        ToolParameter(
            name="notes",
            type="string",
            description="Optional approval notes",
            required=False,
        ),
    ],
    examples=[
        '<task_approve id="phase-b-001">looks good</task_approve>',
    ],
    result_format="Task approved.",
)

# --- task-reject ---
task_reject = ToolDefinition(
    name="task-reject",
    description="Reject a task (used by reviewing agents).",
    category="task",
    risk_level="low",
    requires_permission=False,
    xml_tag="task_reject",
    xml_form="mixed",
    xml_attributes=["id"],
    xml_body_param="reason",
    parameters=[
        ToolParameter(
            name="task_id",
            type="string",
            description="ID of the task to reject",
            required=True,
        ),
        ToolParameter(
            name="reason",
            type="string",
            description="Reason for rejection",
            required=False,
        ),
    ],
    examples=[
        '<task_reject id="phase-b-001">missing test coverage</task_reject>',
    ],
    result_format="Task rejected.",
)


def register_all():
    """Register all task tool definitions."""
    registry = get_registry()
    registry.register(task_checkpoint)
    registry.register(task_complete)
    registry.register(task_approve)
    registry.register(task_reject)


# Auto-register on import
register_all()
