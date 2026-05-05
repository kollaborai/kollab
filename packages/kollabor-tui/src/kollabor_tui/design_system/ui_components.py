"""High-level UI components for message display.

This module provides pre-built UI components using the design system primitives.
These are ready-to-use patterns for common terminal UI elements.
"""

from .components import Box, C, TagBox, solid_fg
from .gradient import gradient
from .theme import S, T

__all__ = ["UI"]


class UI:
    """High-level UI components for terminal chat interfaces.

    Pre-built patterns for banners, status bars, messages, tool calls,
    code blocks, and other common UI elements.

    Example:
        >>> print(UI.banner())
        >>> print(UI.thinking("2.5", tokens=150))
        >>> print(UI.response("Hello! How can I help?"))
    """

    WIDTH = 76  # Standard component width
    INDENT = "  "  # Nested component indent
    NESTED_WIDTH = 50  # Nested component width

    # =========================================================================
    # PRIMARY COMPONENTS
    # =========================================================================

    @classmethod
    def banner(cls):
        """Render app banner with gradient background."""
        logo = [
            "                                                                            ",
            "   ▄█▀▀▀█▄  █ ▄▀ █▀▀█ █   █   █▀▀█ █▀▀▄ █▀▀█ █▀▀█                          ",
            "   ██   ██  █▀▄  █  █ █   █   █▄▄█ █▀▀▄ █  █ █▄▄▀                          ",
            "   ▀█▄▄▄█▀  █  █ █▄▄█ █▄▄ █▄▄ █  █ █▄▄▀ █▄▄█ █ █▄   v0.5.0                 ",
            "                                                                            ",
        ]
        return Box.render(logo, T().primary, T().text_dark, cls.WIDTH)

    @classmethod
    def status(cls, left, mid, right):
        """Render three-section status bar."""
        left_seg = gradient(
            f" {C['lightning']} {left} ", T().primary, T().text_dark, 20
        )
        mid_seg = gradient(f" {C['bullet']} {mid} ", T().response_bg, T().text, 36)
        right_seg = gradient(f" {C['diamond']} {right} ", T().dark, T().text_dim, 20)
        line = left_seg + mid_seg + right_seg
        # Composite gradient for edges
        edge_colors = T().primary[:2] + T().response_bg[:2] + T().dark[:2]
        return f"{Box.top(edge_colors, cls.WIDTH)}\n{line}\n{Box.bottom(edge_colors, cls.WIDTH)}"

    @classmethod
    def user_input(cls, text):
        """Render user input line with prompt tag."""
        return TagBox.render(
            lines=[f" {text}"],
            tag_bg=T().user_tag,

            tag_width=3,
            content_colors=T().input_bg,
            content_fg=T().text,
            content_width=cls.WIDTH - 3,
            tag_chars=[f" {C['prompt']} "],
        )

    @classmethod
    def thinking(cls, seconds, tokens=None):
        """Render thinking indicator."""
        if tokens:
            text = f" thinking {seconds}s  ({tokens} tokens)"
        else:
            text = f" thinking {seconds}s{C['ellipsis']}"
        return TagBox.render(
            lines=[text],
            tag_bg=T().thinking_tag,

            tag_width=3,
            content_colors=T().dark,
            content_fg=T().text_dim,
            content_width=cls.WIDTH - 3,
            tag_chars=[" ~ "],
        )

    @classmethod
    def response(cls, text):
        """Render single LLM response line."""
        return TagBox.render(
            lines=[f" {text}"],
            tag_bg=T().ai_tag,

            tag_width=3,
            content_colors=T().response_bg,
            content_fg=T().text,
            content_width=cls.WIDTH - 3,
            tag_chars=[f" {C['diamond']} "],
        )

    @classmethod
    def response_block(cls, lines):
        """Render multi-line LLM response."""
        content_lines = [f" {line}" for line in lines]
        tag_chars = [f" {C['diamond']} "] + ["   "] * (len(lines) - 1)
        return TagBox.render(
            lines=content_lines,
            tag_bg=T().ai_tag,

            tag_width=3,
            content_colors=T().response_bg,
            content_fg=T().text,
            content_width=cls.WIDTH - 3,
            tag_chars=tag_chars,
        )

    # =========================================================================
    # TOOL COMPONENTS
    # =========================================================================

    @classmethod
    def tool_call(cls, name, args, status="running"):
        """Render tool call block with status indicator."""
        configs = {
            "running": (" * ", T().tool_tag, T().text, T().secondary, T().text),
            "success": (
                f" {C['success']} ",
                T().ai_tag,
                T().text_dark,
                T().success,
                T().text,
            ),
            "error": (f" {C['error']} ", T().error[0], T().text, T().error, T().text),
        }
        icon, tag_bg, tag_fg, content_colors, content_fg = configs.get(
            status, configs["running"]
        )

        return TagBox.render(
            lines=[
                f" {S.BOLD}{name}({args}){S.RESET_BOLD}",
                f" {status}{C['ellipsis']}",
            ],
            tag_bg=tag_bg,
            tag_fg=tag_fg,
            tag_width=3,
            content_colors=content_colors,
            content_fg=content_fg,
            content_width=cls.NESTED_WIDTH - 3,
            tag_chars=[icon, "   "],
            indent=cls.INDENT,
        )

    @classmethod
    def tool_result(cls, lines):
        """Render tool result block (solid background for code/text)."""
        padded = [f"  {line}" for line in lines]
        box = Box.render_solid(padded, T().code_bg, T().text_dim, cls.NESTED_WIDTH)
        return "\n".join(cls.INDENT + line for line in box.split("\n"))

    # =========================================================================
    # CODE & CONTENT BLOCKS
    # =========================================================================

    @classmethod
    def code_block(cls, code_lines, lang="python"):
        """Render code block with language-colored tag."""
        lang_colors = {
            "python": (130, 80, 180),
            "javascript": (240, 220, 80),
            "typescript": (50, 120, 200),
            "rust": (220, 100, 50),
            "go": (80, 180, 220),
            "bash": (100, 180, 100),
            "shell": (100, 180, 100),
        }
        lang_bg = lang_colors.get(lang, (100, 100, 100))
        lang_fg = T().text_dark if lang == "javascript" else T().text

        lines = [f" {S.BOLD}{lang}{S.RESET_BOLD}"] + [f" {line}" for line in code_lines]
        tag_chars = [" # "] + ["   "] * len(code_lines)

        return TagBox.render(
            lines=lines,
            tag_bg=lang_bg,
            tag_fg=lang_fg,
            tag_width=3,
            content_colors=T().code_bg,
            content_fg=T().text,
            content_width=cls.NESTED_WIDTH - 3,
            tag_chars=tag_chars,
            use_gradient=False,
            indent=cls.INDENT,
        )

    @classmethod
    def error(cls, title, message):
        """Render error block."""
        return TagBox.render(
            lines=[f" {S.BOLD}{title}{S.RESET_BOLD}", f" {message}"],
            tag_bg=T().error[0],
            tag_fg=T().text,
            tag_width=3,
            content_colors=T().error,
            content_fg=T().text,
            content_width=cls.NESTED_WIDTH - 3,
            tag_chars=[f" {C['error']} ", "   "],
            indent=cls.INDENT,
        )

    @classmethod
    def warning(cls, message):
        """Render warning block."""
        return TagBox.render(
            lines=[f" {message}"],
            tag_bg=T().warning[0],

            tag_width=3,
            content_colors=T().warning,
            content_fg=T().text_dark,
            content_width=cls.NESTED_WIDTH - 3,
            tag_chars=[f" {C['warning']} "],
            indent=cls.INDENT,
        )

    @classmethod
    def info(cls, message):
        """Render info block."""
        return TagBox.render(
            lines=[f" {message}"],
            tag_bg=T().secondary[0],

            tag_width=3,
            content_colors=T().secondary,
            content_fg=T().text_dark,
            content_width=cls.NESTED_WIDTH - 3,
            tag_chars=[f" {C['info']} "],
            indent=cls.INDENT,
        )

    @classmethod
    def success(cls, message):
        """Render success block."""
        return TagBox.render(
            lines=[f" {message}"],
            tag_bg=T().success[0],

            tag_width=3,
            content_colors=T().success,
            content_fg=T().text_dark,
            content_width=cls.NESTED_WIDTH - 3,
            tag_chars=[f" {C['success']} "],
            indent=cls.INDENT,
        )

    # =========================================================================
    # DIVIDERS & DECORATIONS
    # =========================================================================

    @classmethod
    def divider(cls, label=""):
        """Render section divider with optional label."""
        if label:
            left = C["line_h"] * 2
            right = C["line_h"] * (cls.WIDTH - len(label) - 8)
            return solid_fg(
                f" {left}{C['t_right']} {label} {C['t_left']}{right}", T().text_dim
            )
        return solid_fg(f" {C['line_h'] * (cls.WIDTH - 2)}", T().text_dim)

    @classmethod
    def section_header(cls, title):
        """Render section header with gradient."""
        return TagBox.render(
            lines=[f" {S.BOLD}{title}{S.RESET_BOLD}"],
            tag_bg=T().primary[0],

            tag_width=3,
            content_colors=T().dark[0],
            content_fg=T().text,
            content_width=cls.WIDTH - 3,
            tag_chars=[f" {C['square']} "],
            use_gradient=False,
        )
