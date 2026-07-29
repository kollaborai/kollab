"""On-demand tool loading definitions.

Gateway tools that let agents discover and load tools at runtime.
tool-search finds tools by keyword; tool-load injects a tool's full
definition into the active session. Together these reduce context
overhead — agents see a compact summary instead of all 96+ MCP tool
schemas upfront.
"""

from ..tool_definition import ToolDefinition, ToolParameter
from ..tool_registry import get_registry

tool_search = ToolDefinition(
    name="tool-search",
    description=(
        "Search for available tools by keyword. Returns a compact list "
        "of matching tools (name, category, and one-line description) "
        "without loading their full schemas. Use this to discover tools "
        "you need, then call tool-load to activate them."
    ),
    category="on_demand",
    risk_level="low",
    requires_permission=False,
    xml_tag="tool-search",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="query",
            type="string",
            description=(
                "Search query — matched against tool names, categories, "
                "and descriptions. Case-insensitive substring match. "
                "If omitted, returns ALL available tools (full catalog)."
            ),
            required=False,
        ),
    ],
    examples=[
        "<tool-search><query>file</query></tool-search>",
        "<tool-search><query>database</query></tool-search>",
        "<tool-search><query>mcp github</query></tool-search>",
    ],
    result_format=(
        "List of matching tools, each with: name, category, source "
        "(built-in or MCP server name), and a one-line description. "
        "Results are ranked by relevance. Empty list if no matches."
    ),
    error_modes=[
        "No matches: query returned zero tools",
        "Registry error: tool registry not initialized",
    ],
    notes=(
        "This searches ALL registered tools — both built-in (64) and "
        "MCP-discovered (96+). The results are metadata only: use "
        "tool-load to get the full schema and activate a tool."
    ),
    safety_features=[
        "read-only — does not modify the active tool set",
        "results are compact (one line per tool)",
    ],
    key_rules=[
        "use broad queries first (e.g. 'file') then narrow down",
        "MCP tools show their source server name",
        "follow up with tool-load on any tool you want to use",
    ],
)

tool_load = ToolDefinition(
    name="tool-load",
    description=(
        "Load a tool's full definition into the active session. "
        "After loading, the tool is available for use immediately. "
        "Use tool-search first to find the tool name you need."
    ),
    category="on_demand",
    risk_level="medium",
    requires_permission=True,
    xml_tag="tool-load",
    xml_form="nested",
    parameters=[
        ToolParameter(
            name="name",
            type="string",
            description=(
                "Exact tool name to load (e.g. 'mcp:github:create_issue'). "
                "Use tool-search to find available tool names."
            ),
            required=True,
        ),
    ],
    examples=[
        "<tool-load><name>mcp:github:create_issue</name></tool-load>",
        "<tool-load><name>mcp:filesystem:read_file</name></tool-load>",
    ],
    result_format=(
        "On success: confirmation with the loaded tool's name, full "
        "parameter schema, and usage examples. The tool is now active "
        "for the rest of the session. On failure: error message "
        "explaining why the tool could not be loaded."
    ),
    error_modes=[
        "Tool not found: name does not match any registered tool",
        "Already loaded: tool is already in the active set",
        "MCP server offline: tool's source server is not connected",
    ],
    notes=(
        "Loaded tools remain active until session end. The full schema "
        "(parameters, examples, error modes) is injected into context "
        "so the agent can use the tool correctly. This is the on-demand "
        "loading mechanism that keeps the system prompt compact."
    ),
    safety_features=[
        "validates tool exists in the registry before loading",
        "requires permission (medium risk — adds new capabilities)",
        "MCP tools require their source server to be connected",
    ],
    key_rules=[
        "use tool-search to find the exact tool name first",
        "loaded tools persist for the rest of the session",
        "loading a tool already in the active set is a no-op",
    ],
)


def register_all():
    """Register all on-demand tool definitions."""
    registry = get_registry()
    registry.register(tool_search)
    registry.register(tool_load)


# Auto-register on import
register_all()
