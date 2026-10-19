"""Clean message renderer -- foreground markers, content plain.

Resize-safe alternative to ModernMessageRenderer. The tag column
(3 chars with icon) uses foreground color from the theme.
The content area is plain terminal text with no background color
and no width padding, so terminal resize never causes wrap artifacts.

     ◆ response text, no background color
       second line, just plain text
"""

import logging
from typing import List, Optional

from kollabor_tui._tool_detail import (
    _agent_marker,
    _assistant_text_color,
    _clean_tool_detail,
    _format_tool_row,
    _normalize_tool_label,
    _parse_markdown_bold,
    _truncate_plain,
)
from kollabor_tui.color_contrast import readable_agent_color
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
    continuation_prefix = (
        continuation_prefix if continuation_prefix is not None else (" " * len(prefix))
    )

    for line_idx, line in enumerate(lines):
        active_prefix = prefix if line_idx == 0 else continuation_prefix
        available = max(1, line_width - len(active_prefix))
        # Parse markdown bold before wrapping: wrap_text preserves ANSI codes
        # and carries active codes across line breaks, so bold survives wrap.
        parsed_line = _parse_markdown_bold(line) if line else ""
        wrapped = wrap_text(parsed_line, available) if parsed_line else [""]

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
        return prefix + solid_fg(
            _truncate_plain(label, available_for_text), label_color
        )

    available = max(1, available_for_text - len(label) - 1)
    return (
        prefix
        + solid_fg(label, label_color)
        + " "
        + solid_fg(_truncate_plain(detail, available), T().text)
    )


class CleanRenderer:
    """Clean renderer -- foreground tag markers, plain content.

    Implements the MessageRendererProtocol. Resize-safe because content
    area has no background color or width padding.
    """

    TAG_WIDTH = 3

    def user_message(self, content: str, width: Optional[int] = None) -> str:
        return "\n" + _plain_rows(
            _content_lines(content),
            " ▌ ",
            T().user_tag,
            T().text,
            width,
        )

    def assistant_message(self, content: str, width: Optional[int] = None) -> str:
        return self.response_block(_content_lines(content), width)

    def response_block(self, lines: List[str], width: Optional[int] = None) -> str:
        return "\n" + _plain_rows(
            lines,
            " ◆ ",
            T().text,
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
        return _plain_rows(message.split("\n"), "ℹ ", T().text_dim, T().text_dim, width)

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
        prefix = _agent_marker(tag_char, observing)
        text_color = readable_agent_color(
            agent_color,
            background=T().dark[0],
            target=T().text,
            muted_target=T().text_dim,
            observing=observing,
        )
        return "\n" + _plain_rows(
            content.split("\n"),
            prefix,
            text_color,
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
