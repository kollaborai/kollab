"""MCP tool definitions.

Tools for managing MCP server connections at runtime.
"""

from ..tool_definition import ToolDefinition
from ..tool_registry import get_registry

mcp_reload = ToolDefinition(
    name="mcp-reload",
    description=(
        "Reload MCP server connections and rediscover tools. "
        "Use this when MCP configuration has changed or new servers "
        "have been added. Closes all active connections, reloads "
        "config files, and reconnects enabled servers."
    ),
    category="mcp",
    risk_level="medium",
    requires_permission=True,
    xml_tag="mcp-reload",
    xml_form="body",
    parameters=[],
    examples=[
        "<mcp-reload />",
        "<mcp-reload></mcp-reload>",
    ],
    result_format=(
        "On success: summary with configured, discovered, and "
        "reconnected server counts. On failure: error message."
    ),
    error_modes=[
        "MCP integration not available (mcp_integration is None)",
        "Config file parse error",
        "Server connection failures (partial success possible)",
    ],
    notes=(
        "This is the runtime equivalent of the /mcp reload slash command. "
        "It allows agents to pick up new MCP servers without requiring a "
        "full application restart."
    ),
    safety_features=[
        "Only reloads enabled servers from config",
        "Graceful shutdown of existing connections before reconnect",
    ],
    key_rules=[
        "no parameters needed — reloads all configured servers",
        "check the returned counts to verify servers connected",
    ],
)


def register_all():
    """Register all MCP tool definitions."""
    registry = get_registry()
    registry.register(mcp_reload)


# Auto-register on import
register_all()
