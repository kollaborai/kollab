"""Scratchpad tool definitions.

Ephemeral notepad that survives context compaction but not sessions.
"""

from ..tool_definition import ToolDefinition, ToolParameter
from ..tool_registry import get_registry


# --- scratchpad (overwrite) ---
scratchpad = ToolDefinition(
    name="scratchpad",
    description="Overwrite scratchpad with new content. Survives context compaction.",
    category="scratchpad",
    risk_level="low",
    requires_permission=False,
    xml_tag="scratchpad",
    xml_form="body",
    xml_body_param="content",
    parameters=[
        ToolParameter(
            name="content",
            type="string",
            description="Content to write to scratchpad (replaces existing)",
            required=True,
        ),
    ],
    examples=[
        "<scratchpad>working on phase B of tool registry migration</scratchpad>",
    ],
    result_format="Confirmation that scratchpad was updated.",
    key_rules=[
        "use scratchpad for: current task notes, work-in-progress tracking, temporary reminders",
        "use vault for: permanent knowledge that survives sessions",
        "scratchpad survives context compaction but NOT sessions",
    ],
)

# --- scratchpad-append ---
scratchpad_append = ToolDefinition(
    name="scratchpad-append",
    description="Append content to the existing scratchpad.",
    category="scratchpad",
    risk_level="low",
    requires_permission=False,
    xml_tag="scratchpad_append",
    xml_form="body",
    xml_body_param="content",
    parameters=[
        ToolParameter(
            name="content",
            type="string",
            description="Content to append to scratchpad",
            required=True,
        ),
    ],
    examples=[
        "<scratchpad_append>found bug in hub.py line 520</scratchpad_append>",
    ],
    result_format="Confirmation that content was appended.",
    key_rules=[
        "use for incremental notes without losing existing scratchpad content",
    ],
)

# --- scratchpad-clear ---
scratchpad_clear = ToolDefinition(
    name="scratchpad-clear",
    description="Wipe the scratchpad contents.",
    category="scratchpad",
    risk_level="low",
    requires_permission=False,
    xml_tag="scratchpad_clear",
    xml_form="attributes",
    parameters=[],
    examples=[
        "<scratchpad_clear />",
    ],
    result_format="Confirmation that scratchpad was cleared.",
)

# --- scratchpad-get ---
scratchpad_get = ToolDefinition(
    name="scratchpad-get",
    description="Read the current scratchpad contents.",
    category="scratchpad",
    risk_level="low",
    requires_permission=False,
    xml_tag="scratchpad_get",
    xml_form="attributes",
    parameters=[],
    examples=[
        "<scratchpad_get />",
    ],
    result_format="Current scratchpad contents, or message that scratchpad is empty.",
)


def register_all():
    """Register all scratchpad tool definitions."""
    registry = get_registry()
    registry.register(scratchpad)
    registry.register(scratchpad_append)
    registry.register(scratchpad_clear)
    registry.register(scratchpad_get)


# Auto-register on import
register_all()
