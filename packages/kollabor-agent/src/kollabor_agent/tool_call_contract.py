"""Shared native tool-call normalization for CLI and engine paths."""

import logging
import re
from typing import Any, Mapping

logger = logging.getLogger(__name__)


def _get_tool_call_field(tool_call: Any, key: str, default: Any = None) -> Any:
    if isinstance(tool_call, Mapping):
        return tool_call.get(key, default)
    return getattr(tool_call, key, default)


def _tool_call_input(tool_call: Any) -> dict[str, Any]:
    raw = (
        _get_tool_call_field(tool_call, "input")
        or _get_tool_call_field(tool_call, "arguments")
        or {}
    )
    return raw if isinstance(raw, dict) else {}


def _clean_tool_name(tool_name: str, known_names: set[str]) -> str:
    """Recover a useful name from malformed native tool-call names."""
    if "<" not in tool_name and ">" not in tool_name:
        return tool_name

    logger.warning("Malformed tool name detected: %s", tool_name[:100])
    match = re.search(r"<tool_call>([^<]+)", tool_name)
    if match:
        return match.group(1).strip()

    for known_name in known_names:
        if known_name in tool_name:
            return known_name

    return tool_name


def _registry_native_names() -> set[str]:
    try:
        from .tool_registry import get_registry

        return {tool.native_name for tool in get_registry().list()}
    except Exception:
        logger.debug("Tool registry unavailable during tool-call normalization")
        return set()


def normalize_native_tool_call(
    tool_call: Any,
    *,
    mcp_tool_names: set[str] | None = None,
    plugin_handler_names: set[str] | None = None,
) -> dict[str, Any]:
    """Normalize a provider native tool call into ToolExecutor input.

    The agent-facing schema list can contain built-in registry tools,
    plugin-backed tools, and MCP tools. ToolExecutor dispatches on ``type``,
    so both the engine and TUI path must classify the advertised native name
    the same way.
    """
    mcp_tool_names = mcp_tool_names or set()
    plugin_handler_names = plugin_handler_names or set()
    known_names = set(mcp_tool_names) | set(plugin_handler_names) | _registry_native_names()

    tool_name = str(_get_tool_call_field(tool_call, "name", "") or "")
    tool_name = _clean_tool_name(tool_name, known_names)
    raw_type = str(_get_tool_call_field(tool_call, "type", "tool_use") or "tool_use")
    input_value = _tool_call_input(tool_call)

    plugin_key = tool_name
    if plugin_key not in plugin_handler_names:
        plugin_key = tool_name.replace("-", "_")

    registry_names = _registry_native_names()
    if tool_name in mcp_tool_names:
        resolved_type = "mcp_tool"
    elif plugin_key in plugin_handler_names:
        resolved_type = plugin_key
    elif tool_name in registry_names:
        resolved_type = tool_name
    elif raw_type not in ("tool_use", "function"):
        resolved_type = raw_type
    else:
        resolved_type = "mcp_tool"

    normalized = {
        "type": resolved_type,
        "id": _get_tool_call_field(tool_call, "id", ""),
        "name": tool_name,
        "input": input_value,
        "arguments": input_value,
    }
    if resolved_type != "mcp_tool":
        normalized.update(input_value)
    if isinstance(tool_call, Mapping):
        return {**dict(tool_call), **normalized}
    return normalized
