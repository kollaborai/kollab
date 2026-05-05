"""Generate markdown documentation from ToolDefinitions.

Rendered into agent system prompts so the model knows how to
write the correct XML syntax. Replaces the hand-maintained
markdown files in bundles/agents/_base/sections/tool-reference/.
"""

from ..tool_definition import ToolDefinition
from ..tool_registry import ToolRegistry, get_registry

# Shared category titles — single source of truth
CATEGORY_TITLES = {
    "file_ops": "File Operations",
    "terminal": "Terminal",
    "hub": "Hub (Agent Mesh)",
    "scratchpad": "Scratchpad",
    "task": "Task Lifecycle",
    "wait": "Waiting for Input",
    "curate": "Context Curation",
}


def render_tool_markdown(tool: ToolDefinition) -> str:
    """Render one tool's markdown documentation."""
    lines: list[str] = []

    tag = tool.xml_tag_name
    lines.append(f"### `<{tag}>` — {tool.description}")
    lines.append("")

    # Parameters table
    if tool.parameters:
        lines.append("Parameters:")
        lines.append("")
        for param in tool.parameters:
            req = " (required)" if param.required else ""
            default = ""
            if param.default is not None:
                default = f" (default: `{param.default}`)"
            lines.append(
                f"- `{param.name}` ({param.type}){req}{default}: "
                f"{param.description}"
            )
        lines.append("")

    # Examples
    if tool.examples:
        lines.append("Examples:")
        lines.append("")
        lines.append("```")
        for example in tool.examples:
            lines.append(example)
        lines.append("```")
        lines.append("")

    # Result format
    if tool.result_format:
        lines.append("Returns:")
        lines.append("")
        lines.append(tool.result_format)
        lines.append("")

    # Error modes
    if tool.error_modes:
        lines.append("Error modes:")
        lines.append("")
        for err in tool.error_modes:
            lines.append(f"- {err}")
        lines.append("")

    # Safety features
    if tool.safety_features:
        lines.append("Safety features:")
        lines.append("")
        for feat in tool.safety_features:
            lines.append(f"  [ok] {feat}")
        lines.append("")

    # Key rules
    if tool.key_rules:
        lines.append("Key rules:")
        lines.append("")
        for i, rule in enumerate(tool.key_rules, 1):
            lines.append(f"  [{i}] {rule}")
        lines.append("")

    # Anti-patterns
    if tool.anti_patterns:
        lines.append("Anti-patterns:")
        lines.append("")
        for pattern in tool.anti_patterns:
            lines.append(pattern)
        lines.append("")

    # Notes
    if tool.notes:
        lines.append("Notes:")
        lines.append("")
        lines.append(tool.notes)
        lines.append("")

    return "\n".join(lines)


def render_for_bundle(
    allowed_tools: list[str],
    registry: ToolRegistry = None,
) -> str:
    """Render markdown for the tools allowed in a bundle.

    Groups by category, renders each category section.

    Args:
        allowed_tools: Tool names from bundle's agent.json.
        registry: Optional registry instance.

    Returns:
        Full markdown documentation for the bundle's system prompt.
    """
    if registry is None:
        registry = get_registry()
    tools = registry.get_for_bundle(allowed_tools)

    # Group by category
    by_category: dict[str, list[ToolDefinition]] = {}
    for tool in tools:
        by_category.setdefault(tool.category, []).append(tool)

    lines = [
        "# Tool Reference",
        "",
        "The tools below are available to you in this session. "
        "Emit them in your response content using the XML syntax "
        "shown in the examples. Tool results are returned as "
        "user-role messages prefixed with `Tool result: [tool_name]`.",
        "",
    ]

    for category in sorted(by_category.keys()):
        cat_tools = by_category[category]
        title = CATEGORY_TITLES.get(category, category.title())
        lines.append(f"## {title}")
        lines.append("")

        for tool in cat_tools:
            lines.append(render_tool_markdown(tool))
            lines.append("---")
            lines.append("")

    # Remove trailing separator
    while lines and lines[-1] in ("---", ""):
        lines.pop()

    return "\n".join(lines)
