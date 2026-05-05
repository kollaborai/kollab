"""Generate XML regex patterns from ToolDefinitions.

Produces compiled regex patterns matching XML tags in assistant
content. Drop-in replacement for the hand-written patterns in
response_parser.py.
"""

import re
from typing import Dict

from ..tool_definition import ToolDefinition


def build_regex_for_tool(tool: ToolDefinition) -> str:
    """Build the regex pattern that matches this tool's XML form.

    Does NOT compile — returns the pattern string.

    Args:
        tool: The ToolDefinition.

    Returns:
        A regex pattern string suitable for re.compile().
    """
    tag = re.escape(tool.xml_tag_name)

    if tool.xml_form == "body":
        # <tag>body</tag> or <tag attrs>body</tag>
        if tool.xml_attributes:
            attr_group = r"(?:\s+[^>]*?)?"
        else:
            attr_group = r"\s*"
        return rf"<{tag}{attr_group}>(.*?)</{tag}>"

    if tool.xml_form == "nested":
        # <tag><sub1>...</sub1><sub2>...</sub2></tag>
        return rf"<{tag}>(.*?)</{tag}>"

    if tool.xml_form == "attributes":
        # <tag attr="val"/> or <tag attr="val" /> (self-closing, with or without space before />)
        # also matches bare <tag/> when no attributes present
        return rf"<{tag}(?:\s+([^>]*?))?\s*/>"

    if tool.xml_form == "mixed":
        # <tag attr="val">body</tag>
        return rf"<{tag}\s*([^>]*?)>(.*?)</{tag}>"

    raise ValueError(f"Unknown xml_form: {tool.xml_form}")


def generate_all_regexes(
    tools: list[ToolDefinition] = None,
) -> Dict[str, re.Pattern]:
    """Generate compiled regex patterns for tools.

    Args:
        tools: Optional list of ToolDefinitions. If None, uses
               the global registry.

    Returns:
        Dict mapping canonical tool name to compiled regex.
    """
    if tools is None:
        from ..tool_registry import get_registry
        tools = get_registry().list()

    result = {}
    for tool in tools:
        pattern = build_regex_for_tool(tool)
        result[tool.name] = re.compile(pattern, re.DOTALL | re.IGNORECASE)
    return result
