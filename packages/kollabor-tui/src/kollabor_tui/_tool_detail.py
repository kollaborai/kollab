"""Tool-row and color helpers shared by the clean and modern renderers."""

import re
import shlex
from typing import Optional

from kollabor_tui.design_system import S, T, solid_fg


def _parse_markdown_bold(text: str) -> str:
    """Parse markdown bold syntax (**bold**) and convert to ANSI codes.

    Args:
        text: Text containing markdown bold syntax

    Returns:
        Text with **bold** converted to ANSI bold codes
    """
    # Pattern to match **bold** syntax (non-greedy)
    pattern = r"\*\*(.*?)\*\*"

    # Replace with ANSI bold codes
    return re.sub(pattern, f"{S.BOLD}\\1{S.RESET_BOLD}", text)


def _truncate_plain(text: str, width: int) -> str:
    """Truncate unstyled text to a visible width."""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


def _mix_color(
    first: tuple[int, int, int],
    second: tuple[int, int, int],
    first_weight: float = 0.65,
) -> tuple[int, int, int]:
    """Blend two RGB colors into a quieter accent."""
    second_weight = 1 - first_weight
    return tuple(
        int(first[i] * first_weight + second[i] * second_weight) for i in range(3)
    )


def _assistant_text_color() -> tuple[int, int, int]:
    """Muted assistant foreground, distinct from user/tool text."""
    return getattr(T(), "assistant_text", _mix_color(T().ai_tag, T().text_dim, 0.68))


def _tool_summary_color(status: str, summary: str) -> tuple[int, int, int]:
    normalized = summary.strip().lower()
    if status == "error" or normalized.startswith("error"):
        return T().error[0]
    if status == "running":
        return T().warning[0]
    if normalized == "success":
        return T().success[0]
    return T().text_dim


def _normalize_tool_label(label: str) -> str:
    normalized = label.strip().lower().replace("-", "_")
    aliases = {
        "read": "file_read",
        "read_file": "file_read",
        "file": "file_read",
        "search_file": "file_read",
        "file_search": "file_read",
        "grep": "file_read",
        "edit_file": "file_edit",
        "patch_file": "file_edit",
        "apply_patch": "file_edit",
        "modify_file": "file_edit",
        "file_modify": "file_edit",
        "write_file": "file_write",
        "create_file": "file_create",
        "append_file": "file_write",
        "add_file": "file_create",
        "new_file": "file_create",
        "file_create": "file_create",
        "file_append": "file_write",
        "remove_file": "file_delete",
        "file_delete": "file_delete",
        "delete_file": "file_delete",
        "copy_file": "file_copy",
        "file_copy": "file_copy",
        "file_move": "file_move",
        "move_file": "file_move",
        "rename_file": "file_move",
        "file_list": "file_list",
        "list_files": "file_list",
        "ls": "file_list",
        "glob": "file_list",
        "find_files": "file_list",
        "shell": "terminal",
        "bash": "terminal",
        "command": "terminal",
        "run_command": "terminal",
        "exec": "terminal",
        "exec_command": "terminal",
        "subprocess": "terminal",
        "hub": "hub_msg",
        "hub_message": "hub_msg",
        "hub_send": "hub_msg",
        "hub_broadcast": "hub_msg",
        "agent_message": "hub_msg",
        "send_agent": "hub_msg",
        "hub_agents": "hub_agents",
        "agent_hub": "hub_agents",
        "agents": "hub_agents",
        "list_agents": "hub_agents",
        "mcp": "mcp_tool",
        "mcp_tool": "mcp_tool",
        "mcp_call": "mcp_tool",
        "state": "state_update",
        "state_service": "state_update",
        "state_update": "state_update",
        "context": "context",
        "context_update": "context",
        "todo": "task",
        "task_update": "task",
    }
    return aliases.get(normalized, normalized or "tool")


def _named_arg(detail: str, key: str) -> Optional[str]:
    try:
        parts = shlex.split(detail)
    except ValueError:
        return None

    prefix = f"{key}="
    for part in parts:
        if part.startswith(prefix):
            return part[len(prefix) :]
    return None


def _clean_tool_detail(label: str, detail: str) -> str:
    if label.startswith("file_"):
        path = _named_arg(detail, "path")
        if path:
            return path
        file_path = _named_arg(detail, "file_path")
        if file_path:
            return file_path
        file_value = _named_arg(detail, "file")
        if file_value:
            return file_value
    if label == "terminal":
        command = _named_arg(detail, "command")
        if command:
            return command
        cmd = _named_arg(detail, "cmd")
        if cmd:
            return cmd
    return detail


def _tool_badge(label: str) -> str:
    normalized = label.lower()
    if normalized in {"terminal", "shell", "bash", "command"}:
        return ">_"
    if normalized in {"file_read", "read", "file", "read_file", "search"}:
        return "⌕"
    if normalized in {"write", "file_write", "file_append"}:
        return "✎"
    if normalized in {"create", "file_create"}:
        return "✚"
    if normalized in {"edit", "file_edit"}:
        return "✐"
    if normalized in {"file_delete", "delete", "remove"}:
        return "✕"
    if normalized in {"file_move", "file_copy", "move", "copy", "rename"}:
        return "↦"
    if normalized in {"file_list", "list", "ls", "glob"}:
        return "☷"
    if normalized in {"hub_msg", "hub_message", "hub_send", "hub_broadcast"}:
        return "✉"
    if normalized in {"hub_agents", "agent_hub", "agents", "list_agents"}:
        return "◎"
    if normalized in {"mcp_tool", "mcp", "mcp_call"}:
        return "⌘"
    if normalized in {"state_update", "state", "state_service"}:
        return "↻"
    if normalized in {"task", "todo", "plan"}:
        return "☑"
    if normalized in {"context", "context_update", "compact"}:
        return "☷"
    if "hub" in normalized or "agent" in normalized:
        return "◎"
    if "mcp" in normalized:
        return "⌘"
    return "•"


def _tool_badge_color(label: str, status: str) -> tuple[int, int, int]:
    if status == "error":
        return T().error[0]
    normalized = label.lower()
    if normalized == "terminal":
        return T().tool_tag
    if normalized == "file_read":
        return T().ai_tag
    if normalized in {"file_write", "file_create", "file_edit"}:
        return T().user_tag
    if normalized in {"hub_msg", "hub_agents", "mcp_tool"} or "hub" in normalized:
        return T().ai_tag
    if normalized in {"file_delete"}:
        return T().error[0]
    return T().text_dim


def _render_tool_badge(label: str, status: str) -> str:
    badge = _tool_badge(label)
    if badge == ">_":
        return solid_fg(">", _tool_badge_color(label, status)) + solid_fg(
            "_", T().text_dim
        )
    return solid_fg(badge, _tool_badge_color(label, status))


def _format_tool_summary(status: str, summary: Optional[str]) -> Optional[str]:
    if summary is None:
        return None
    if status == "error":
        match = re.search(r"(?:exit\s+code|code)\s+(-?\d+)", summary, re.I)
        if match:
            return f"✖ Exit code {match.group(1)}"
    return summary


def _format_tool_row(
    label: str,
    detail: str,
    status: str,
    width: int,
    result_summary: Optional[str],
) -> str:
    indent = "      "
    badge = _tool_badge(label)
    detail_color = T().text
    available = max(1, width - len(indent) - len(badge) - 1)

    display_text = _format_tool_summary(status, result_summary)
    if display_text is None and status == "running":
        display_text = "running..."
    elif display_text is None and status == "error":
        display_text = "error"

    detail_text = detail or label
    summary_text = ""
    if display_text:
        suffix_width = len(" ➲ ") + len(display_text)
        if suffix_width >= available:
            summary_width = max(1, available - len(" ➲ "))
            summary_text = _truncate_plain(display_text, summary_width)
            detail_text = ""
        else:
            summary_text = display_text
            detail_text = _truncate_plain(detail_text, available - suffix_width)
    else:
        detail_text = _truncate_plain(detail_text, available)

    rendered = indent + _render_tool_badge(label, status)
    if detail_text:
        rendered += " " + solid_fg(detail_text, detail_color)
    if summary_text:
        rendered += solid_fg(" ➲ ", T().text_dim) + solid_fg(
            summary_text,
            _tool_summary_color(status, summary_text),
        )
    return rendered


def _agent_marker(tag_char: str, observing: bool) -> str:
    marker = tag_char.strip()
    if marker in {">", "~", ""}:
        marker = "◇" if observing else "◆"
    return f" {marker} "
