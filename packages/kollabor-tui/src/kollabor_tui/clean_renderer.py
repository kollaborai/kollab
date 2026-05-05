"""Clean message renderer -- tag column colored, content plain.

Resize-safe alternative to ModernMessageRenderer. The tag column
(3 chars with icon) uses solid background color from the theme.
The content area is plain terminal text with no background color
and no width padding, so terminal resize never causes wrap artifacts.

    ┌───┐
    | * | response text, no background color
    |   | second line, just plain text
    └───┘
"""

import logging
from typing import List, Optional

from kollabor_tui.design_system import C, S, T, solid, solid_fg, wrap_text
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


class CleanRenderer:
    """Clean renderer -- colored tag column, plain content.

    Implements the MessageRendererProtocol. Resize-safe because content
    area has no background color or width padding.
    """

    TAG_WIDTH = 3

    def user_message(self, content: str, width: Optional[int] = None) -> str:
        cw = _content_width(self.TAG_WIDTH, width)
        lines = _expand_lines(_content_lines(content), cw)
        output = []
        tag_bg = T().user_tag
        tag_fg = T().text_dark
        output.append(_tag_edge(tag_bg))
        for i, line in enumerate(lines):
            icon = " ❯ " if i == 0 else "   "
            output.append(_tag(icon, tag_bg, tag_fg) + " " + line)
        output.append(_tag_edge(tag_bg, top=False))
        return "\n".join(output)

    def assistant_message(self, content: str, width: Optional[int] = None) -> str:
        cw = _content_width(self.TAG_WIDTH, width)
        lines = _expand_lines(_content_lines(content), cw)
        output = []
        tag_bg = T().ai_tag
        tag_fg = T().text_dark
        output.append(_tag_edge(tag_bg))
        for i, line in enumerate(lines):
            icon = " ◆ " if i == 0 else "   "
            output.append(_tag(icon, tag_bg, tag_fg) + " " + line)
        output.append(_tag_edge(tag_bg, top=False))
        return "\n".join(output)

    def response_block(self, lines: List[str], width: Optional[int] = None) -> str:
        cw = _content_width(self.TAG_WIDTH, width)
        lines = _expand_lines(lines, cw)
        output = []
        tag_bg = T().ai_tag
        tag_fg = T().text_dark
        output.append(_tag_edge(tag_bg))
        for i, line in enumerate(lines):
            icon = " ◆ " if i == 0 else "   "
            output.append(_tag(icon, tag_bg, tag_fg) + " " + line)
        output.append(_tag_edge(tag_bg, top=False))
        return "\n".join(output)

    def tool_call(
        self,
        name: str,
        args: str,
        status: str = "running",
        nested_width: Optional[int] = None,
        result_summary: Optional[str] = None,
    ) -> str:
        configs = {
            "running": (f" {C['tool_running']} ", T().tool_tag, T().text, "running..."),
            "success": (f" {C['tool_success']} ", T().ai_tag, T().text_dark, ""),
            "error": (f" {C['tool_error']} ", T().error[0], T().text, "error"),
        }
        icon, tag_bg, tag_fg, status_text = configs.get(status, configs["running"])

        display_text = result_summary if result_summary else status_text
        cw = _content_width(self.TAG_WIDTH, nested_width)
        output = []
        output.append(_tag_edge(tag_bg))
        # Tool name line - wrap if name(args) is very long
        name_lines = _expand_lines([f"{S.BOLD}{name}({args}){S.RESET_BOLD}"], cw)
        output.append(_tag(icon, tag_bg, tag_fg) + " " + name_lines[0])
        for extra in name_lines[1:]:
            output.append(_tag("   ", tag_bg, tag_fg) + " " + extra)
        if display_text:
            for dl in _expand_lines([display_text], cw):
                output.append(_tag("   ", tag_bg, tag_fg) + " " + dl)
        output.append(_tag_edge(tag_bg, top=False))
        return "\n".join(output)

    def tool_result(
        self, lines: List[str], nested_width: Optional[int] = None, wrap: bool = False
    ) -> str:
        # Indent tool results slightly, no background
        output = []
        for line in lines:
            output.append(f"    {line}")
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
        cw = _content_width(self.TAG_WIDTH, nested_width)
        tag_bg = T().error[0]
        tag_fg = T().text
        message_lines = _expand_lines(message.split("\n"), cw)
        output = []
        output.append(_tag_edge(tag_bg))
        output.append(_tag(" x ", tag_bg, tag_fg) + f" {S.BOLD}{title}{S.RESET_BOLD}")
        for line in message_lines:
            output.append(_tag("   ", tag_bg, tag_fg) + " " + line)
        output.append(_tag_edge(tag_bg, top=False))
        return "\n".join(output)

    def warning_block(self, message: str, nested_width: Optional[int] = None) -> str:
        cw = _content_width(self.TAG_WIDTH, nested_width)
        tag_bg = T().warning[0]
        tag_fg = T().text_dark
        message_lines = _expand_lines(message.split("\n"), cw)
        output = []
        output.append(_tag_edge(tag_bg))
        for i, line in enumerate(message_lines):
            icon = " ! " if i == 0 else "   "
            output.append(_tag(icon, tag_bg, tag_fg) + " " + line)
        output.append(_tag_edge(tag_bg, top=False))
        return "\n".join(output)

    def info_block(self, message: str, width: Optional[int] = None) -> str:
        cw = _content_width(self.TAG_WIDTH, width)
        tag_bg = T().secondary[0]
        tag_fg = T().text_dark
        message_lines = _expand_lines(message.split("\n"), cw)
        output = []
        output.append(_tag_edge(tag_bg))
        for i, line in enumerate(message_lines):
            icon = " ℹ " if i == 0 else "   "
            output.append(_tag(icon, tag_bg, tag_fg) + " " + line)
        output.append(_tag_edge(tag_bg, top=False))
        return "\n".join(output)

    def success_block(self, message: str, width: Optional[int] = None) -> str:
        cw = _content_width(self.TAG_WIDTH, width)
        tag_bg = T().success[0]
        tag_fg = T().text_dark
        message_lines = _expand_lines(message.split("\n"), cw)
        output = []
        output.append(_tag_edge(tag_bg))
        for i, line in enumerate(message_lines):
            icon = " ✔ " if i == 0 else "   "
            output.append(_tag(icon, tag_bg, tag_fg) + " " + line)
        output.append(_tag_edge(tag_bg, top=False))
        return "\n".join(output)

    def agent_message(
        self,
        content: str,
        agent_color=None,
        tag_char=" > ",
        observing=False,
        width=None,
    ) -> str:
        cw = _content_width(self.TAG_WIDTH, width)
        if agent_color is None:
            agent_color = T().secondary[0]
        if observing:
            agent_color = tuple(max(c // 3, 15) for c in agent_color)
        tag_fg = T().text_dark
        message_lines = _expand_lines(content.split("\n"), cw)
        output = []
        output.append(_tag_edge(agent_color))
        for i, line in enumerate(message_lines):
            icon = tag_char if i == 0 else "   "
            output.append(_tag(icon, agent_color, tag_fg) + " " + line)
        output.append(_tag_edge(agent_color, top=False))
        return "\n".join(output)

    def thinking_indicator(
        self, seconds: float, tokens: Optional[int] = None, width: Optional[int] = None
    ) -> str:
        tag_bg = T().thinking_tag
        tag_fg = T().text_dark
        text = (
            f"thinking {seconds}s  ({tokens} tokens)"
            if tokens
            else f"thinking {seconds}s..."
        )
        output = []
        output.append(_tag_edge(tag_bg))
        output.append(_tag(" ~ ", tag_bg, tag_fg) + " " + text)
        output.append(_tag_edge(tag_bg, top=False))
        return "\n".join(output)
