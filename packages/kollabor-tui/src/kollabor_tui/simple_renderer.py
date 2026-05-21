"""Simple message renderer -- plain text with unicode prefixes.

No colors, no boxes, no gradients. Just text with minimal prefixes.
Suitable for pipe mode, accessibility, or terminals with no color support.

    > user message
    assistant response
    Error: something broke
    Info: system message
"""

import logging
from typing import List, Optional

from kollabor_tui.clean_renderer import (
    _clean_tool_detail,
    _format_tool_summary,
    _normalize_tool_label,
    _tool_badge,
)

logger = logging.getLogger(__name__)


class SimpleRenderer:
    """Plain text renderer with minimal formatting.

    Implements the MessageRendererProtocol using only plain text
    and unicode prefixes. No ANSI color codes in output.
    """

    def user_message(self, content: str, width: Optional[int] = None) -> str:
        return f"> {content}"

    def assistant_message(self, content: str, width: Optional[int] = None) -> str:
        return content

    def response_block(self, lines: List[str], width: Optional[int] = None) -> str:
        return "\n".join(lines)

    def tool_call(
        self,
        name: str,
        args: str,
        status: str = "running",
        nested_width: Optional[int] = None,
        result_summary: Optional[str] = None,
    ) -> str:
        label = _normalize_tool_label(name)
        detail = _clean_tool_detail(label, args.strip()) or label
        summary = _format_tool_summary(status, result_summary)
        if summary is None and status == "running":
            summary = "running..."
        elif summary is None and status == "error":
            summary = "error"

        line = f"      {_tool_badge(label)} {detail}"
        if summary:
            line += f" ➲ {summary}"
        return line

    def tool_result(
        self, lines: List[str], nested_width: Optional[int] = None, wrap: bool = False
    ) -> str:
        return "\n".join(f"    {line}" for line in lines)

    def code_block(
        self,
        code_lines: List[str],
        lang: str = "python",
        nested_width: Optional[int] = None,
    ) -> str:
        header = f"--- {lang} ---"
        body = "\n".join(f"  {line}" for line in code_lines)
        footer = "-" * len(header)
        return f"{header}\n{body}\n{footer}"

    def error_block(
        self, title: str, message: str, nested_width: Optional[int] = None
    ) -> str:
        return f"error: {title}\n  {message}"

    def warning_block(self, message: str, nested_width: Optional[int] = None) -> str:
        return f"warning: {message}"

    def info_block(self, message: str, width: Optional[int] = None) -> str:
        return f"info: {message}"

    def agent_message(
        self,
        content: str,
        agent_color=None,
        tag_char=" > ",
        observing=False,
        width=None,
    ) -> str:
        prefix = "◇" if observing else "◆"
        return f" {prefix} {content}"

    def success_block(self, message: str, width: Optional[int] = None) -> str:
        return f"ok: {message}"

    def thinking_indicator(
        self, seconds: float, tokens: Optional[int] = None, width: Optional[int] = None
    ) -> str:
        if tokens:
            return f"thinking {seconds}s ({tokens} tokens)"
        return f"thinking {seconds}s..."
