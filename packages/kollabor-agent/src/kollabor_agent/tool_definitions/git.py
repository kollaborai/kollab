"""Git workflow tool definition.

This is a doc-only tool — git operations are executed via the
terminal tool. This definition exists so bundle agent.json files
can list "git" in their tools field without registry warnings.
"""

from ..tool_definition import ToolDefinition, ToolParameter
from ..tool_registry import get_registry

git_tool = ToolDefinition(
    name="git",
    description=(
        "Read-only Git inspection via terminal. A Git-only bundle grant "
        "allows status, diff, log, and show commands; writes and shell "
        "commands require the separate terminal grant."
    ),
    category="file_ops",
    risk_level="medium",
    requires_permission=False,
    xml_tag="terminal",
    xml_form="body",
    xml_body_param="command",
    parameters=[
        ToolParameter(
            name="command",
            type="string",
            description="Git command to execute (e.g. 'git status')",
            required=True,
        ),
    ],
    examples=[
        "<terminal>git status</terminal>",
        "<terminal>git diff</terminal>",
        "<terminal>git log --oneline -5</terminal>",
    ],
    expose_native=False,
    result_format="Read-only Git output via the terminal read-only command policy.",
    notes=(
        "This doc-only tool maps to terminal for bundle scoping. A git "
        "grant is limited to read-only Git inspection; terminal access "
        "must be granted separately for shell commands or Git writes."
    ),
)


def register_all():
    """Register git tool definition."""
    registry = get_registry()
    registry.register(git_tool)


# Auto-register on import
register_all()
