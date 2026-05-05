"""Terminal tool definitions.

All terminal tools: foreground execution, background subprocess sessions,
and session management commands.
"""

from ..tool_definition import ToolDefinition, ToolParameter
from ..tool_registry import get_registry


terminal_tool = ToolDefinition(
    name="terminal",
    description=(
        "Execute a terminal/shell command and return the output."
    ),
    category="terminal",
    risk_level="high",
    requires_permission=True,
    xml_tag="terminal",
    xml_form="mixed",
    xml_body_param="command",
    xml_attributes=["background", "name", "timeout", "cwd"],
    parameters=[
        ToolParameter(
            name="command",
            type="string",
            description="Shell command to execute",
            required=True,
        ),
        ToolParameter(
            name="background",
            type="boolean",
            description=(
                "Run as a persistent subprocess session (default: false)"
            ),
            required=False,
            default=False,
        ),
        ToolParameter(
            name="name",
            type="string",
            description=(
                "Session name when background is true. Used by "
                "terminal-status, terminal-output, terminal-kill."
            ),
            required=False,
        ),
        ToolParameter(
            name="timeout",
            type="string",
            description=(
                "Auto-kill after duration (30s, 5m, 1h). Default 60s."
            ),
            required=False,
        ),
        ToolParameter(
            name="cwd",
            type="string",
            description="Working directory for the command.",
            required=False,
        ),
    ],
    examples=[
        "<terminal>git status</terminal>",
        "<terminal>python -m pytest tests/</terminal>",
        '<terminal background="true" name="dev">npm run dev</terminal>',
        '<terminal background="true" name="build" timeout="10m">npm run build</terminal>',
    ],
    result_format=(
        "On success: command stdout (raw). On non-zero exit: "
        "'Exit code N: <stderr>'."
    ),
    error_modes=[
        "Exit code <N>: <stderr>",
        "Command timed out after <N> seconds",
        "Permission denied",
    ],
    notes=(
        "Background sessions run as persistent subprocesses and "
        "persist across turns. Use terminal-status, terminal-output, "
        "terminal-kill to manage them. Foreground terminal has a "
        "default timeout of 60 seconds."
    ),
    safety_features=[
        "background sessions run as subprocesses with auto-kill timeouts",
        "foreground commands have 60s default timeout",
    ],
    key_rules=[
        "use background=true for dev servers, build scripts, and long-running processes",
        "use foreground (default) for git, pytest, pip, and quick commands",
        "user can view live sessions with /terminal view <name>",
    ],
)

terminal_status = ToolDefinition(
    name="terminal-status",
    description="List or check status of background terminal sessions.",
    category="terminal",
    risk_level="low",
    requires_permission=False,
    xml_tag="terminal-status",
    xml_form="body",
    xml_body_param="name",
    parameters=[
        ToolParameter(
            name="name",
            type="string",
            description=(
                "Session name to check, or '*' to list all sessions"
            ),
            required=True,
        ),
    ],
    examples=[
        "<terminal-status>*</terminal-status>",
        "<terminal-status>dev</terminal-status>",
    ],
    result_format="Status of matching session(s).",
)

terminal_output = ToolDefinition(
    name="terminal-output",
    description="Capture recent output from a background terminal session.",
    category="terminal",
    risk_level="low",
    requires_permission=False,
    xml_tag="terminal-output",
    xml_form="mixed",
    xml_body_param="name",
    xml_attributes=["lines"],
    parameters=[
        ToolParameter(
            name="name",
            type="string",
            description="Session name to capture output from",
            required=True,
        ),
        ToolParameter(
            name="lines",
            type="integer",
            description="Number of recent lines to capture (default: 50)",
            required=False,
            default=50,
        ),
    ],
    examples=[
        "<terminal-output>dev</terminal-output>",
        '<terminal-output lines="100">dev</terminal-output>',
    ],
    result_format="Recent output from the session.",
)

terminal_kill = ToolDefinition(
    name="terminal-kill",
    description="Kill a background terminal session or all sessions.",
    category="terminal",
    risk_level="medium",
    requires_permission=False,
    xml_tag="terminal-kill",
    xml_form="body",
    xml_body_param="name",
    parameters=[
        ToolParameter(
            name="name",
            type="string",
            description="Session name to kill, or '*' to kill all",
            required=True,
        ),
    ],
    examples=[
        "<terminal-kill>dev</terminal-kill>",
        "<terminal-kill>*</terminal-kill>",
    ],
    result_format="Confirmation that session(s) were killed.",
)


def register_all():
    """Register all terminal tool definitions."""
    registry = get_registry()
    registry.register(terminal_tool)
    registry.register(terminal_status)
    registry.register(terminal_output)
    registry.register(terminal_kill)


# Auto-register on import
register_all()
