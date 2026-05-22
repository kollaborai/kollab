"""Tool display formatting utilities for LLM tool execution results.

Extracted from kollabor.llm.message_display_service.MessageDisplayService.

NOTE: This module contains extracted private methods (_prefix) to maintain
exact behavioral compatibility. Public APIs (without _ prefix) are wrappers
for convenience.

TODO: After extraction verification, consolidate duplicate methods:
- _format_tool_result vs _get_tool_result_summary (different ANSI handling)
- _extract_tool_info vs _extract_tool_name_args (nearly identical)
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from .visual_effects import ColorPalette

logger = logging.getLogger(__name__)


def _get_file_path(args: Dict) -> str:
    """Extract file path from tool arguments, checking all known param name variants.

    Built-in tools use "file", MCP servers commonly use "file_path" or "path".
    """
    return args.get("file_path") or args.get("file") or args.get("path") or ""


def _is_read_like_tool_name(tool_name: str) -> bool:
    normalized = tool_name.lower().replace("-", "_")
    return normalized in {"read", "readfile", "file_read", "read_file"}


# =============================================================================
# EXTRACTED FROM: kollabor/llm/message_display_service.py
# Original lines: 167-249
# =============================================================================


def format_tool_header(result: Any, tool_data: Optional[Dict] = None) -> str:
    """Format tool execution header with consistent styling.

    Extracted from MessageDisplayService._format_tool_header

    Args:
        result: Tool execution result
        tool_data: Original tool data for command/name extraction

    Returns:
        Formatted tool header string
    """
    # Tool indicator with dynamic color support
    indicator = f"{ColorPalette.BRIGHT_LIME}⏺{ColorPalette.RESET}"

    if result.tool_type == "terminal":
        # XML tools: {"type": "terminal", "command": "..."}
        # Native API tools: {"name": "terminal", "arguments": {"command": "..."}}
        command = None
        if tool_data:
            command = tool_data.get("command") or tool_data.get("arguments", {}).get(
                "command"
            )
        command = command or (result.tool_id if not tool_data else "unknown")
        return f"{indicator} terminal({truncate_tool_args(command)})"
    elif result.tool_type == "mcp_tool":
        # Extract tool name and arguments from original tool data
        tool_name = tool_data.get("name", "unknown") if tool_data else result.tool_id
        arguments = tool_data.get("arguments", {}) if tool_data else {}

        # Clean up malformed tool names (may contain XML from confused LLM)
        if "<" in tool_name or ">" in tool_name:
            # Try to extract clean tool name
            match = re.search(r"<([^<]+)", tool_name)
            if match:
                tool_name = match.group(1).strip()
            else:
                # Find last word that looks like a tool name
                words = re.findall(r"\b([a-z_]+)\b", tool_name.lower())
                tool_name = words[-1] if words else "mcp_tool"

        # For Read-like tools, show file path with line info
        if _is_read_like_tool_name(tool_name):
            file_path = _get_file_path(arguments)
            offset = arguments.get("offset")
            limit = arguments.get("limit")
            truncated_path = truncate_tool_args(file_path)
            if offset is not None or limit is not None:
                offset_val = offset if offset is not None else 0
                if limit:
                    return f"{indicator} {tool_name}({truncated_path}, lines {offset_val + 1}-{offset_val + limit})"
                else:
                    return f"{indicator} {tool_name}({truncated_path}, from line {offset_val + 1})"
            return f"{indicator} {tool_name}({truncated_path})"

        # Format arguments cleanly
        if arguments:
            # Show key arguments inline, truncate long values
            arg_parts = []
            for k, v in list(arguments.items())[:3]:  # Max 3 args
                v_str = str(v)
                if len(v_str) > 30:
                    v_str = v_str[:27] + "..."
                arg_parts.append(f'{k}="{v_str}"')
            args_display = ", ".join(arg_parts)
            if len(arguments) > 3:
                args_display += f", +{len(arguments) - 3} more"
            return f"{indicator} {tool_name}({truncate_tool_args(args_display)})"
        else:
            return f"{indicator} {tool_name}()"
    elif result.tool_type.startswith("file_"):
        # Extract filename/path from file operation data
        display_info = extract_file_display_info(tool_data, result.tool_type, result)
        return f"{indicator} {result.tool_type}({truncate_tool_args(display_info)})"
    elif tool_data:
        # Generic tool: show tool_type with first meaningful argument
        args = {k: v for k, v in tool_data.items() if k not in ("type", "id")}
        if args:
            first_key = next(iter(args))
            first_val = str(args[first_key])
            if len(first_val) > 40:
                first_val = first_val[:37] + "..."
            return f"{indicator} {result.tool_type}({truncate_tool_args(first_val)})"
        return f"{indicator} {result.tool_type}()"
    else:
        # Fallback: try tool_data for name/arguments before showing raw call ID
        if tool_data:
            tool_name = tool_data.get("name", result.tool_type or "tool")
            arguments = tool_data.get("arguments", {})
            if arguments:
                arg_parts = []
                for k, v in list(arguments.items())[:2]:
                    v_str = str(v)
                    if len(v_str) > 30:
                        v_str = v_str[:27] + "..."
                    arg_parts.append(f'{k}="{v_str}"')
                args_display = ", ".join(arg_parts)
                return f"{indicator} {tool_name}({truncate_tool_args(args_display)})"
            return f"{indicator} {tool_name}()"
        return f"{indicator} {result.tool_type}({truncate_tool_args(result.tool_id)})"


# =============================================================================
# EXTRACTED FROM: kollabor/llm/message_display_service.py
# Original lines: 251-302
# =============================================================================


def extract_file_display_info(
    tool_data: Optional[Dict],
    tool_type: str,
    result: Optional[Any] = None,
) -> str:
    """Extract display information from file operation data.

    Extracted from MessageDisplayService._extract_file_display_info

    Args:
        tool_data: Original tool data
        tool_type: Type of file operation
        result: Tool execution result (optional, for output fallback)

    Returns:
        Filename or path to display
    """
    file_path = None

    if tool_data:
        # Most file operations use 'file' key
        if "file" in tool_data:
            file_path = tool_data["file"]
        # Move/copy operations use 'from' and 'to'
        elif "from" in tool_data and "to" in tool_data:
            return f"{tool_data['from']} → {tool_data['to']}"
        # mkdir/rmdir use 'path'
        elif "path" in tool_data:
            file_path = tool_data["path"]
        # Native API tools: {"name": "file_read", "arguments": {"file_path": "..."}}
        elif "arguments" in tool_data and isinstance(tool_data["arguments"], dict):
            args = tool_data["arguments"]
            file_path = _get_file_path(args)
            if "from" in args and "to" in args:
                return f"{args['from']} → {args['to']}"

    # Fallback: parse filepath from result output
    if not file_path and result and result.output:
        # Match patterns from file_operations_executor.py outputs:
        # "✓ Created {path} ({size} bytes)"
        # "✓ Edited {path}"
        # "✓ Read X lines from {path}"
        # "✓ Deleted {path}"
        # "✓ Appended to {path}"
        patterns = [
            r"Created (/?\S+)",  # file_create
            r"Edited (/?\S+)",  # file_edit
            r"Read \d+ lines from (/?\S+)",  # file_read
            r"Deleted (/?\S+)",  # file_delete
            r"Appended to (/?\S+)",  # file_append
            r"Directory created: (/?\S+)",  # file_mkdir
            r"Directory removed: (/?\S+)",  # file_rmdir
        ]
        for pattern in patterns:
            match = re.search(pattern, result.output)
            if match:
                file_path = match.group(1)
                break

    return file_path or "unknown"


# =============================================================================
# EXTRACTED FROM: kollabor/llm/message_display_service.py
# Original lines: 304-393
# =============================================================================


def format_tool_result(result: Any, tool_data: Optional[Dict] = None) -> str:
    """Format tool execution result summary.

    Extracted from MessageDisplayService._format_tool_result

    Args:
        result: Tool execution result
        tool_data: Original tool data for request info (optional)

    Returns:
        Formatted result summary string
    """
    if result.success:
        # Count output characteristics for summary
        output_lines = result.output.count("\n") + 1 if result.output else 0
        output_chars = len(result.output) if result.output else 0

        if result.tool_type == "terminal" and result.output:
            return f"\033[32m ▮ Read {output_lines} lines ({output_chars} chars)\033[0m"
        elif result.tool_type == "file_read" and result.output:
            # Extract line count and optional range from output
            # Format: "✓ Read X lines from path (lines N-M):" or "✓ Read X lines from path:"
            match = re.search(
                r"Read (\d+) lines from .+?(?:\(lines ([^)]+)\))?:", result.output
            )
            if match:
                line_count = match.group(1)
                lines_from_output = match.group(2)  # May be None

                # Build line range from various sources
                lines_spec = None
                if lines_from_output:
                    lines_spec = lines_from_output
                elif tool_data:
                    if tool_data.get("lines"):
                        lines_spec = tool_data["lines"]
                    elif (
                        tool_data.get("offset") is not None
                        or tool_data.get("limit") is not None
                    ):
                        # Calculate range from offset/limit
                        offset = tool_data.get("offset", 0)
                        limit = tool_data.get("limit")
                        start = offset + 1  # 1-indexed for display
                        if limit:
                            lines_spec = f"{start}-{start + int(line_count) - 1}"
                        else:
                            lines_spec = f"{start}+"

                if lines_spec:
                    return f"\033[32m ▮ Read {line_count} lines (lines {lines_spec})\033[0m"
                return f"\033[32m ▮ Read {line_count} lines\033[0m"
            return "\033[32m ▮ Success\033[0m"
        elif result.tool_type == "mcp_tool" and result.output:
            # Try to summarize JSON output
            try:
                import json

                data = json.loads(result.output)
                if isinstance(data, dict):
                    # Count items in response
                    if "content" in data:
                        content = data["content"]
                        if isinstance(content, list):
                            return f"\033[32m ▮ Returned {len(content)} items\033[0m"
                        elif isinstance(content, str):
                            preview = (
                                content[:40] + "..." if len(content) > 40 else content
                            )
                            return f"\033[32m ▮ {preview}\033[0m"
                    # Count top-level keys
                    keys = list(data.keys())[:3]
                    return f"\033[32m ▮ Returned {{{', '.join(keys)}{'...' if len(data) > 3 else ''}}}\033[0m"
                elif isinstance(data, list):
                    return f"\033[32m ▮ Returned {len(data)} items\033[0m"
            except (json.JSONDecodeError, TypeError):
                pass
            # Fallback to text preview
            preview = result.output[:50].replace("\n", " ")
            if len(result.output) > 50:
                preview += "..."
            return f"\033[32m ▮ {preview}\033[0m"
        else:
            return "\033[32m ▮ Success\033[0m"
    else:
        return f"\033[31m ▮ Error: {result.error}\033[0m"


# =============================================================================
# EXTRACTED FROM: kollabor/llm/message_display_service.py
# Original lines: 395-471
# =============================================================================


def extract_tool_info(result: Any, tool_data: Optional[Dict] = None) -> Tuple[str, str]:
    """Extract tool name and arguments for modern rendering.

    Extracted from MessageDisplayService._extract_tool_info

    Args:
        result: Tool execution result
        tool_data: Original tool data

    Returns:
        Tuple of (tool_name, tool_args)
    """
    if result.tool_type == "terminal":
        # XML tools: {"type": "terminal", "command": "..."}
        # Native API tools: {"name": "terminal", "arguments": {"command": "..."}}
        command = None
        if tool_data:
            command = tool_data.get("command") or tool_data.get("arguments", {}).get(
                "command"
            )
        command = command or (result.tool_id if not tool_data else "unknown")
        return ("terminal", truncate_tool_args(command))

    elif result.tool_type == "mcp_tool":
        tool_name = tool_data.get("name", "unknown") if tool_data else result.tool_id
        arguments = tool_data.get("arguments", {}) if tool_data else {}

        # Clean up malformed tool names
        if "<" in tool_name or ">" in tool_name:
            match = re.search(r"<([^<]+)", tool_name)
            if match:
                tool_name = match.group(1).strip()
            else:
                words = re.findall(r"\b([a-z_]+)\b", tool_name.lower())
                tool_name = words[-1] if words else "mcp_tool"

        # Format arguments as readable string
        if arguments:
            # For file read, show just the path
            if _is_read_like_tool_name(tool_name):
                return (tool_name, truncate_tool_args(_get_file_path(arguments)))
            # For other tools, show key args
            arg_parts = []
            for k, v in list(arguments.items())[:2]:
                if isinstance(v, str) and len(v) > 30:
                    v = v[:30] + "..."
                arg_parts.append(f"{k}={v}")
            args_str = ", ".join(arg_parts)
            return (tool_name, truncate_tool_args(args_str))

        return (tool_name, "")

    elif result.tool_type == "file_grep":
        # Special handling for grep - shows pattern and path
        # Args may be at top level or nested under "arguments"
        args = tool_data if tool_data else {}
        if "arguments" in args and isinstance(args["arguments"], dict):
            args = args["arguments"]
        pattern = args.get("pattern", "")
        path = args.get("path", args.get("file", "."))
        return ("file_grep", f'"{pattern}" in {truncate_tool_args(path)}')

    elif result.tool_type.startswith("file_"):
        # Use shared helper with output fallback for all other file operations
        display_info = extract_file_display_info(tool_data, result.tool_type, result)
        return (result.tool_type, truncate_tool_args(display_info))

    elif result.tool_type == "malformed_file_op":
        # Extract the operation name from tool_data or result
        operation = (
            tool_data.get("operation", "unknown") if tool_data else result.tool_id
        )
        return (f"malformed_{operation}", "")

    # Fallback: try tool_data for name/arguments before showing raw call ID
    if tool_data:
        tool_name = tool_data.get("name", result.tool_type or "tool")
        arguments = tool_data.get("arguments", {})
        if arguments:
            arg_parts = []
            for k, v in list(arguments.items())[:2]:
                if isinstance(v, str) and len(v) > 30:
                    v = v[:30] + "..."
                arg_parts.append(f"{k}={v}")
            args_str = ", ".join(arg_parts)
            return (tool_name, truncate_tool_args(args_str))
        return (tool_name, "")
    return (
        result.tool_type or "tool",
        truncate_tool_args(result.tool_id or ""),
    )


# =============================================================================
# EXTRACTED FROM: kollabor/llm/message_display_service.py
# Original lines: 473-544
# NOTE: This is a NEAR-DUPLICATE of extract_tool_info above
# Keeping both to preserve exact behavior during extraction
# =============================================================================


def extract_tool_name_args(
    result: Any, tool_data: Optional[Dict] = None
) -> Tuple[str, str]:
    """Extract tool name and arguments for modern rendering.

    Extracted from MessageDisplayService._extract_tool_name_args

    NOTE: This is nearly identical to extract_tool_info but has subtle differences:
    - Uses str(v) instead of checking isinstance(v, str) before truncating
    - Uses 40 char limit instead of 30 for arg values
    - Uses double quotes around arg values: k="v" vs k=v
    - Different handling of mcp_tool arguments

    Args:
        result: Tool execution result
        tool_data: Original tool data

    Returns:
        Tuple of (tool_name, tool_args_string)
    """
    if result.tool_type == "terminal":
        # XML tools: {"type": "terminal", "command": "..."}
        # Native API tools: {"name": "terminal", "arguments": {"command": "..."}}
        command = None
        if tool_data:
            command = tool_data.get("command") or tool_data.get("arguments", {}).get(
                "command"
            )
        command = command or (result.tool_id if not tool_data else "unknown")
        return ("terminal", truncate_tool_args(command))

    elif result.tool_type == "mcp_tool":
        tool_name = tool_data.get("name", "unknown") if tool_data else result.tool_id
        arguments = tool_data.get("arguments", {}) if tool_data else {}

        # Clean up malformed tool names
        if "<" in tool_name or ">" in tool_name:
            match = re.search(r"<([^<]+)", tool_name)
            if match:
                tool_name = match.group(1).strip()
            else:
                words = re.findall(r"\b([a-z_]+)\b", tool_name.lower())
                tool_name = words[-1] if words else "mcp_tool"

        # Format arguments
        if _is_read_like_tool_name(tool_name):
            return (tool_name, truncate_tool_args(_get_file_path(arguments)))
        elif arguments:
            arg_parts = []
            for k, v in list(arguments.items())[:2]:
                v_str = str(v)
                if len(v_str) > 40:
                    v_str = v_str[:37] + "..."
                arg_parts.append(f'{k}="{v_str}"')
            args_str = ", ".join(arg_parts)
            return (tool_name, truncate_tool_args(args_str))
        else:
            return (tool_name, "")

    elif result.tool_type.startswith("file_"):
        display_info = extract_file_display_info(tool_data, result.tool_type, result)
        return (result.tool_type, truncate_tool_args(display_info))

    elif result.tool_type == "malformed_file_op":
        # Extract the operation name from tool_data or result
        operation = (
            tool_data.get("operation", "unknown") if tool_data else result.tool_id
        )
        return (f"malformed_{operation}", "")

    else:
        # Fallback: try tool_data for name/arguments before showing raw call ID
        if tool_data:
            tool_name = tool_data.get("name", result.tool_type or "tool")
            arguments = tool_data.get("arguments", {})
            if arguments:
                arg_parts = []
                for k, v in list(arguments.items())[:2]:
                    v_str = str(v)
                    if len(v_str) > 40:
                        v_str = v_str[:37] + "..."
                    arg_parts.append(f'{k}="{v_str}"')
                args_str = ", ".join(arg_parts)
                return (tool_name, truncate_tool_args(args_str))
            return (tool_name, "")
        return (
            result.tool_type or "tool",
            truncate_tool_args(result.tool_id or ""),
        )


# =============================================================================
# EXTRACTED FROM: kollabor/llm/message_display_service.py
# Original lines: 546-565
# =============================================================================


def truncate_tool_args(args: str, max_length: int = 60) -> str:
    """Truncate tool arguments for single-line display.

    Extracted from MessageDisplayService._truncate_tool_args

    Args:
        args: Raw argument string (may contain newlines)
        max_length: Maximum length before truncation

    Returns:
        Truncated string with ellipsis if needed
    """
    # If multi-line, take first line only
    if "\n" in args:
        first_line = args.split("\n")[0]
        return f"{first_line}..."

    # If single line but too long, truncate
    if len(args) > max_length:
        return f"{args[:max_length]}..."

    return args


# =============================================================================
# EXTRACTED FROM: kollabor/llm/message_display_service.py
# Original lines: 567-577
# NOTE: This is just a wrapper that calls get_tool_result_summary
# =============================================================================


def get_result_summary_modern(result: Any, tool_data: Optional[Dict] = None) -> str:
    """Get result summary for inline display in modern tool rendering.

    Extracted from MessageDisplayService._get_result_summary_modern

    This is a wrapper that forwards to get_tool_result_summary.

    Args:
        result: Tool execution result
        tool_data: Original tool data

    Returns:
        Summary string for inline display
    """
    return get_tool_result_summary(result, tool_data)


# =============================================================================
# EXTRACTED FROM: kollabor/llm/message_display_service.py
# Original lines: 579-621
# =============================================================================


def get_tool_result_summary(result: Any, tool_data: Optional[Dict] = None) -> str:
    """Get a clean summary of tool result without ANSI codes.

    Extracted from MessageDisplayService._get_tool_result_summary

    Args:
        result: Tool execution result
        tool_data: Original tool data

    Returns:
        Clean summary string
    """
    if result.success:
        output_lines = result.output.count("\n") + 1 if result.output else 0
        output_chars = len(result.output) if result.output else 0
        first_line = result.output.strip().splitlines()[0] if result.output else ""

        if first_line.lower().startswith("error:"):
            suffix = "..." if len(first_line) > 80 else ""
            return f"{first_line[:80]}{suffix}"

        if result.tool_type == "terminal" and result.output:
            return f"Read {output_lines} lines ({output_chars} chars)"

        elif result.tool_type == "file_read" and result.output:
            match = re.search(r"Read (\d+) lines", result.output)
            if match:
                return f"Read {match.group(1)} lines"
            return "Success"

        elif result.tool_type == "mcp_tool" and result.output:
            return f"Returned {output_chars} chars"

        elif result.tool_type == "file_edit":
            return "File updated"

        return "Success"
    else:
        # For malformed_file_op, extract first line of error for summary
        if result.tool_type == "malformed_file_op" and result.error:
            error_lines = result.error.split("\n")
            first_line = error_lines[0] if error_lines else result.error
            return f"Error: {first_line}"

        # For other errors, show truncated error message
        return f"Error: {result.error[:80]}{'...' if len(result.error) > 80 else ''}"


# =============================================================================
# EXTRACTED FROM: kollabor/llm/message_display_service.py
# Original lines: 623-636
# =============================================================================


def should_show_output(result: Any) -> bool:
    """Determine if tool output should be displayed inline.

    Extracted from MessageDisplayService._should_show_output

    Args:
        result: Tool execution result

    Returns:
        True if output should be shown
    """
    # Always show output for malformed file operations (they have helpful error details)
    if result.tool_type == "malformed_file_op":
        return True

    return result.success and result.output and len(result.output) < 500


# =============================================================================
# EXTRACTED FROM: kollabor/llm/message_display_service.py
# Original lines: 638-669
# =============================================================================


def format_tool_output(result: Any) -> List[str]:
    """Format tool output for inline display.

    Extracted from MessageDisplayService._format_tool_output

    Args:
        result: Tool execution result

    Returns:
        List of formatted output lines
    """
    # Special formatting for file_edit with diff info
    if (
        result.tool_type == "file_edit"
        and hasattr(result, "metadata")
        and result.metadata
        and "diff_info" in result.metadata
    ):
        return format_edit_diff(result)

    # Default formatting for other outputs
    output_lines = result.output.strip().split("\n")
    formatted_lines = []

    # Show first 20 lines with indentation
    for line in output_lines[:20]:
        formatted_lines.append(f"    {line}")

    # Add truncation message if needed
    if len(output_lines) > 20:
        remaining = len(output_lines) - 20
        formatted_lines.append(f"    ... ({remaining} more lines)")

    return formatted_lines


# =============================================================================
# EXTRACTED FROM: kollabor/llm/message_display_service.py
# Original lines: 671-738
# =============================================================================


def format_edit_diff(result: Any) -> List[str]:
    """Format file edit as a pretty condensed diff.

    Extracted from MessageDisplayService._format_edit_diff

    Args:
        result: Tool execution result with diff_info

    Returns:
        List of formatted diff lines
    """
    diff_info = result.metadata.get("diff_info", {})
    find_text = diff_info.get("find", "")
    replace_text = diff_info.get("replace", "")
    line_numbers = diff_info.get(
        "lines", []
    )  # First few line numbers where edit occurred

    formatted_lines = []

    # Show the first line of output (✅ Replaced...)
    first_line = result.output.split("\n")[0]
    formatted_lines.append(f"    {first_line}")

    # Add pretty diff visualization
    formatted_lines.append("")

    # Calculate starting line number for display
    start_line = line_numbers[0] if line_numbers else None

    # Removed lines (red with -) with line numbers
    removed_lines = find_text.split("\n")
    for i, line in enumerate(removed_lines[:3]):  # Show max 3 lines
        if start_line:
            line_num = start_line + i
            formatted_lines.append(f"    \033[31m│- {line_num:4d} {line}\033[0m")
        else:
            formatted_lines.append(f"    \033[31m│- {line}\033[0m")

    if len(removed_lines) > 3:
        formatted_lines.append(
            f"    \033[31m│  ... ({len(removed_lines) - 3} more lines)\033[0m"
        )

    # Separator
    formatted_lines.append("    \033[90m│\033[0m")

    # Added lines (green with +) with line numbers
    added_lines = replace_text.split("\n")
    for i, line in enumerate(added_lines[:3]):  # Show max 3 lines
        if start_line:
            line_num = start_line + i
            formatted_lines.append(f"    \033[32m│+ {line_num:4d} {line}\033[0m")
        else:
            formatted_lines.append(f"    \033[32m│+ {line}\033[0m")

    if len(added_lines) > 3:
        formatted_lines.append(
            f"    \033[32m│  ... ({len(added_lines) - 3} more lines)\033[0m"
        )

    formatted_lines.append("")

    # Add backup info if present
    output_lines = result.output.split("\n")
    for line in output_lines[1:]:  # Skip first line (already shown)
        if line.strip():
            formatted_lines.append(f"    {line}")

    return formatted_lines


# =============================================================================
# ADD TO __init__.py:
# =============================================================================
# After extraction is complete, add these to packages/kollabor-tui/src/kollabor_tui/__init__.py:
#
# In the imports section:
# from .tool_display import (
#     format_tool_header,
#     extract_file_display_info,
#     format_tool_result,
#     extract_tool_info,
#     extract_tool_name_args,
#     truncate_tool_args,
#     get_result_summary_modern,
#     get_tool_result_summary,
#     should_show_output,
#     format_tool_output,
#     format_edit_diff,
# )
#
# In __all__:
#     "format_tool_header",
#     "extract_file_display_info",
#     "format_tool_result",
#     "extract_tool_info",
#     "extract_tool_name_args",
#     "truncate_tool_args",
#     "get_result_summary_modern",
#     "get_tool_result_summary",
#     "should_show_output",
#     "format_tool_output",
#     "format_edit_diff",
#
# NOTE: Do NOT edit __init__.py directly - other agents may be editing it too.
# The maintainer will merge these additions after all agents finish.
