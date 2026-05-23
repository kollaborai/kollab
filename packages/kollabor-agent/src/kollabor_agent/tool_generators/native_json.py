"""Generate native JSON tool schemas from ToolDefinitions.

OpenAI/xAI/OpenRouter: {type: "function", function: {name, description, parameters}}
Anthropic: {name, description, input_schema}
"""

from typing import Any, Dict, List

from ..tool_registry import ToolRegistry, get_registry


def generate_openai_tools(
    tool_names: List[str],
    registry: ToolRegistry = None,
) -> List[Dict[str, Any]]:
    """Generate OpenAI-family tool schemas from registry entries.

    Args:
        tool_names: Canonical tool names (hyphenated) to include.
        registry: Optional registry instance (defaults to global).

    Returns:
        List of {type: "function", function: {...}} dicts.
    """
    if registry is None:
        registry = get_registry()
    result = []
    for name in tool_names:
        tool = registry.get(name)
        if tool is None:
            continue
        schema = tool.to_json_schema()
        result.append({
            "type": "function",
            "function": schema,
        })
    return result


def generate_anthropic_tools(
    tool_names: List[str],
    registry: ToolRegistry = None,
) -> List[Dict[str, Any]]:
    """Generate Anthropic tool schemas from registry entries.

    Anthropic uses 'input_schema' instead of 'parameters' and
    does not wrap in a 'function' envelope.

    Args:
        tool_names: Canonical tool names (hyphenated) to include.
        registry: Optional registry instance (defaults to global).

    Returns:
        List of {name, description, input_schema} dicts.
    """
    if registry is None:
        registry = get_registry()
    result = []
    for name in tool_names:
        tool = registry.get(name)
        if tool is None:
            continue
        schema = tool.to_json_schema()
        result.append({
            "name": schema["name"],
            "description": schema["description"],
            "input_schema": schema["parameters"],
        })
    return result
