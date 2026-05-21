"""Clean message renderer -- foreground markers, content plain.

Resize-safe alternative to ModernMessageRenderer. The tag column
(3 chars with icon) uses foreground color from the theme.
The content area is plain terminal text with no background color
and no width padding, so terminal resize never causes wrap artifacts.

     ◆ response text, no background color
       second line, just plain text
"""

import logging
import re
import shlex
from typing import List, Optional

from kollabor_tui.design_system import S, T, solid, solid_fg, wrap_text
from kollabor_tui.design_system.border_style import get_border_style
from kollabor_tui.terminal_state import get_global_width

logger = logging.getLogger(__name__)


def _tag(icon: str, bg, fg) -> str:
    """Render a 3-char tag cell with solid background."""
    return str(solid(icon, bg, fg, 3))


def _tag_edge(bg, width: int = 3, top: bool = True) -> str:
    """Render tag column top/bottom edge."""
    style = get_border_style()
    char = style.top if top else style.bottom
    return str(solid_fg(char * width, bg))


def _content_lines(content: str) -> List[str]:
    """Split content into lines."""
    return content.split("\n")


def _expand_lines(raw_lines: List[str], content_width: int) -> List[str]:
    """Wrap lines to fit within content_width, preserving ANSI codes.

    Flattens wrapped results so the caller can iterate with the same
    i == 0 icon logic -- wrapping just adds more lines, all getting
    blank tags.
    """
    if content_width <= 0:
        return raw_lines
    result: List[str] = []
    for line in raw_lines:
        result.extend(wrap_text(line, content_width))
    return result


def _content_width(tag_width: int, width: Optional[int] = None) -> int:
    """Available chars for content after the tag column + space."""
    return (width or get_global_width()) - tag_width - 1


def _plain_rows(
    lines: List[str],
    prefix: str,
    prefix_color,
    text_color,
    width: Optional[int] = None,
    continuation_prefix: Optional[str] = None,
    prefix_bg=None,
) -> str:
    """Render compact rows with a foreground-colored icon only."""
    output: List[str] = []
    line_width = width or get_global_width()
    continuation_prefix = continuation_prefix if continuation_prefix is not None else (
        " " * len(prefix)
    )

    for line_idx, line in enumerate(lines):
        active_prefix = prefix if line_idx == 0 else continuation_prefix
        available = max(1, line_width - len(active_prefix))
        wrapped = wrap_text(line, available) if line else [""]

        for wrap_idx, wrapped_line in enumerate(wrapped):
            row_prefix = active_prefix if wrap_idx == 0 else " " * len(active_prefix)
            rendered = ""
            if row_prefix:
                if prefix_bg and row_prefix.strip():
                    rendered += solid(row_prefix, prefix_bg, prefix_color)
                elif row_prefix.strip():
                    rendered += solid_fg(row_prefix, prefix_color)
                else:
                    rendered += row_prefix
            if wrapped_line:
                rendered += solid_fg(wrapped_line, text_color)
            output.append(rendered)

    return "\n".join(output)


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


def _tool_symbol(label: str, status: str) -> str:
    if status == "error":
        return "!"
    normalized = label.lower()
    if normalized in {"terminal", "shell", "bash", "command"}:
        return "$"
    if normalized in {"file_read", "read", "file", "read_file"}:
        return "#"
    if "hub" in normalized or "agent" in normalized or "mcp" in normalized:
        return "@"
    if normalized in {"write", "edit", "file_edit"}:
        return "+"
    return "*"


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


def _tool_headline(
    symbol: str,
    label: str,
    detail: str,
    width: int,
    symbol_color: tuple[int, int, int],
    label_color: tuple[int, int, int],
) -> str:
    """Render a compact mixed-color tool headline."""
    prefix = solid_fg(symbol, symbol_color) + " "
    available_for_text = max(1, width - len(symbol) - 1)
    if not detail:
        return prefix + solid_fg(_truncate_plain(label, available_for_text), label_color)

    available = max(1, available_for_text - len(label) - 1)
    return (
        prefix
        + solid_fg(label, label_color)
        + " "
        + solid_fg(_truncate_plain(detail, available), T().text)
    )


def _agent_marker(tag_char: str, observing: bool) -> str:
    marker = tag_char.strip()
    if marker in {">", "~", ""}:
        marker = "◇" if observing else "◆"
    return f" {marker} "


class CleanRenderer:
    """Clean renderer -- foreground tag markers, plain content.

    Implements the MessageRendererProtocol. Resize-safe because content
    area has no background color or width padding.
    """

    TAG_WIDTH = 3

    def user_message(self, content: str, width: Optional[int] = None) -> str:
        return _plain_rows(
            _content_lines(content),
            " ▌ ",
            T().user_tag,
            T().text,
            width,
        )

    def assistant_message(self, content: str, width: Optional[int] = None) -> str:
        return self.response_block(_content_lines(content), width)

    def response_block(self, lines: List[str], width: Optional[int] = None) -> str:
        return _plain_rows(
            lines,
            " ֎ ",
            T().ai_tag,
            _assistant_text_color(),
            width,
        )

    def tool_call(
        self,
        name: str,
        args: str,
        status: str = "running",
        nested_width: Optional[int] = None,
        result_summary: Optional[str] = None,
    ) -> str:
        label = _normalize_tool_label(name)
        detail = _clean_tool_detail(label, args.strip())
        width = nested_width or get_global_width()
        return _format_tool_row(label, detail, status, width, result_summary)

    def tool_result(
        self, lines: List[str], nested_width: Optional[int] = None, wrap: bool = False
    ) -> str:
        # Indent and dim raw tool output so it does not compete with the answer.
        output = []
        for line in lines:
            output.append("      " + solid_fg(line, T().text_dim))
        return "\n".join(output)

    def code_block(
        self,
        code_lines: List[str],
        lang: str = "python",
        nested_width: Optional[int] = None,
    ) -> str:
        lang_colors = {
            "python": (130, 80, 180),
            "javascript": (240, 220, 80),
            "typescript": (50, 120, 200),
            "rust": (220, 100, 50),
            "go": (80, 180, 220),
            "bash": (100, 150, 100),
            "shell": (100, 150, 100),
            "json": (200, 150, 50),
            "yaml": (150, 100, 150),
            "markdown": (100, 100, 200),
        }
        lang_bg = lang_colors.get(lang.lower(), (100, 100, 100))
        lang_fg = T().text_dark if lang == "javascript" else T().text

        cw = _content_width(self.TAG_WIDTH, nested_width)
        output = []
        output.append(_tag_edge(lang_bg))
        output.append(_tag(" # ", lang_bg, lang_fg) + f" {S.BOLD}{lang}{S.RESET_BOLD}")
        # Char-wrap (not word-wrap) to preserve code structure
        for line in code_lines:
            for wl in wrap_text(line, cw, word_wrap=False):
                output.append(_tag("   ", lang_bg, lang_fg) + " " + wl)
        output.append(_tag_edge(lang_bg, top=False))
        return "\n".join(output)

    def error_block(
        self, title: str, message: str, nested_width: Optional[int] = None
    ) -> str:
        return _plain_rows(
            [title] + message.split("\n"),
            "✖ ",
            T().error[0],
            T().text,
            nested_width,
        )

    def warning_block(self, message: str, nested_width: Optional[int] = None) -> str:
        return _plain_rows(
            message.split("\n"),
            "⚠ ",
            T().warning[0],
            T().text,
            nested_width,
        )

    def info_block(self, message: str, width: Optional[int] = None) -> str:
        # Temporarily keep timing/reasoning rows plain if they reach this renderer.
        # MessageDisplayService currently converts them into blank spacer rows.
        return _plain_rows(
            message.split("\n"), "ℹ ", T().text_dim, T().text_dim, width
        )

    def success_block(self, message: str, width: Optional[int] = None) -> str:
        return _plain_rows(
            message.split("\n"), "✔ ", T().success[0], T().success[0], width
        )

    def agent_message(
        self,
        content: str,
        agent_color=None,
        tag_char=" > ",
        observing=False,
        width=None,
    ) -> str:
        if agent_color is None:
            agent_color = T().secondary[0]
        if observing:
            agent_color = tuple(max(c // 3, 15) for c in agent_color)
        prefix = _agent_marker(tag_char, observing)
        text_color = agent_color
        return _plain_rows(
            content.split("\n"),
            prefix,
            agent_color,
            text_color,
            width,
        )

    def thinking_indicator(
        self, seconds: float, tokens: Optional[int] = None, width: Optional[int] = None
    ) -> str:
        text = (
            f"thinking {seconds}s  ({tokens} tokens)"
            if tokens
            else f"thinking {seconds}s..."
        )
        return _plain_rows([text], "~ ", T().thinking_tag, T().text, width)
