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
        "Git version control operations. Executed via terminal. "
        "Provides workflow guidance for commits, branches, diffs."
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
        "<terminal>git add -A && git commit -m 'descriptive message'</terminal>",
    ],
    result_format="Standard git command output via terminal.",
    notes=(
        "Git operations use the terminal tool. This is a doc-only "
        "tool definition that maps to terminal for bundle scoping."
    ),
)


def register_all():
    """Register git tool definition."""
    registry = get_registry()
    registry.register(git_tool)


# Auto-register on import
register_all()
