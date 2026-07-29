"""Workspace tool definitions.

Tool for switching the agent's working directory at runtime.
"""

from ..tool_definition import ToolDefinition, ToolParameter
from ..tool_registry import get_registry

workspace_set = ToolDefinition(
    name="workspace-set",
    description=(
        "Switch the agent's working directory. Updates the workspace "
        "for all subsequent terminal commands and file operations. "
        "The status bar cwd widget updates immediately."
    ),
    category="workspace",
    risk_level="medium",
    requires_permission=True,
    xml_tag="workspace-set",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="path",
            type="string",
            description=(
                "Absolute or relative path to the new working directory. "
                "Must exist and be a directory."
            ),
            required=True,
        ),
    ],
    examples=[
        "<workspace-set><path>/Users/me/projects/other-app</path></workspace-set>",
        "<workspace-set><path>../sibling-project</path></workspace-set>",
    ],
    result_format=(
        "On success: confirmation message with the resolved absolute path. "
        "On failure: error message explaining why the switch failed."
    ),
    error_modes=[
        "Directory not found: path does not exist",
        "Not a directory: path exists but is a file",
        "Permission denied: cannot access the path",
    ],
    notes=(
        "This changes the persistent workspace for the agent session. "
        "All file operations (read, edit, create, etc.) and terminal "
        "commands without an explicit cwd will resolve against the new "
        "directory. Use a relative path to move relative to the current "
        "workspace, or an absolute path to jump anywhere."
    ),
    safety_features=[
        "validates path exists and is a directory before switching",
        "expands ~ and resolves relative paths before applying",
    ],
    key_rules=[
        "path must be an existing directory",
        "relative paths resolve against the current workspace",
        "the switch is session-scoped — does not persist across restarts",
    ],
)


def register_all():
    """Register all workspace tool definitions."""
    registry = get_registry()
    registry.register(workspace_set)


# Auto-register on import
register_all()
