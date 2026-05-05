"""Wait-for-user tool definition.

Stops the agent from continuing until the user responds.
Critical for preventing autonomous loops.
"""

from ..tool_definition import ToolDefinition, ToolParameter
from ..tool_registry import get_registry


wait_for_user = ToolDefinition(
    name="wait-for-user",
    description=(
        "Signal that you are done acting and want to wait for "
        "user input. Use this to avoid autonomous loops when "
        "you have no more work to do."
    ),
    category="wait",
    risk_level="low",
    requires_permission=False,
    xml_tag="wait_for_user",
    xml_form="mixed",
    xml_body_param="message",
    xml_attributes=["message"],
    parameters=[
        ToolParameter(
            name="message",
            type="string",
            description="Optional message to display while waiting",
            required=False,
        ),
    ],
    examples=[
        "<wait_for_user />",
        "<wait_for_user>standing by for next task</wait_for_user>",
    ],
    result_format=(
        "Acknowledgement that agent is waiting. No further agent "
        "turns until user responds."
    ),
    notes=(
        "Use wait_for_user when you've completed a task and have "
        "nothing else to do. This prevents the agent from spinning "
        "in a loop. Also used when asking a question and needing "
        "a user response before continuing."
    ),
    key_rules=[
        "emit when you have completed the task, are blocked and need external input, or notice you are in a loop with another agent",
        "do NOT emit when you are mid-task and about to do more work, or waiting for a tool result (system handles that)",
        "combine with <task_complete> in the same turn when finishing a task",
    ],
    safety_features=[
        "starts a 60-second cooldown — peer messages during cooldown are blocked (coordinator can still reach you)",
        "messages with force='true' can still reach you during cooldown",
    ],
)


def register_all():
    """Register wait tool definition."""
    registry = get_registry()
    registry.register(wait_for_user)


# Auto-register on import
register_all()
