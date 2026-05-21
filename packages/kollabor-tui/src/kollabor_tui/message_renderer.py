"""Message rendering system for conversation display.

This module provides message rendering for conversation display,
including display filter registry for plugin extensibility
and the ModernMessageRenderer (TagBox + gradient based rendering).

The renderer protocol (MessageRendererProtocol) defines the interface
that all renderers implement. See renderer_protocol.py.
"""

import logging
import threading
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

# Design system imports for modern rendering
from kollabor_tui.design_system import (
    Box,
    S,
    T,
    TagBox,
    solid_fg,
    wrap_text,
)
from kollabor_tui.terminal_state import get_global_width

# Lazy import to avoid circular dependency with kollabor.io
# tool_spinner is in kollabor.io which imports from kollabor_tui.terminal_renderer


def _get_tool_spinner():
    """Lazy import of tool_spinner to avoid circular dependency."""
    from kollabor_tui.tool_spinner import get_tool_spinner

    return get_tool_spinner()


def _is_tool_spinner_enabled():
    """Lazy import of tool_spinner to avoid circular dependency."""
    from kollabor_tui.tool_spinner import is_tool_spinner_enabled

    return is_tool_spinner_enabled()


logger = logging.getLogger(__name__)


class DisplayFilterRegistry:
    """Registry for message display filters.

    Plugins can register filter functions that transform message content
    before display. This allows plugins to strip/transform their own
    XML commands without hardcoding patterns in core.

    Example:
        # In plugin initialize():
        DisplayFilterRegistry.register(
            "my_plugin",
            self._strip_my_xml,
            message_types=[MessageType.ASSISTANT]
        )

        # Filter function signature:
        def _strip_my_xml(content: str, message_type: 'MessageType') -> str:
            return re.sub(r"<my-tag>.*?</my-tag>", "", content)
    """

    _filters: Dict[str, Dict[str, Any]] = {}
    _sorted_filters_cache: Optional[List[Tuple[str, Dict[str, Any]]]] = None
    _lock = threading.RLock()

    @classmethod
    def register(
        cls,
        name: str,
        filter_fn: Callable[[str, "MessageType"], str],
        message_types: Optional[List["MessageType"]] = None,
        priority: int = 100,
    ) -> None:
        """Register a display filter.

        Args:
            name: Unique name for this filter (usually plugin name).
            filter_fn: Function that takes (content, message_type) and returns transformed content.
            message_types: List of message types to apply filter to (None = all types).
            priority: Execution priority (higher = runs first).
        """
        with cls._lock:
            cls._filters[name] = {
                "fn": filter_fn,
                "message_types": message_types,
                "priority": priority,
            }
            # Invalidate cache when filters change
            cls._sorted_filters_cache = None
            logger.debug(f"Registered display filter: {name}")

    @classmethod
    def unregister(cls, name: str) -> None:
        """Unregister a display filter.

        Args:
            name: Name of the filter to remove.
        """
        with cls._lock:
            if name in cls._filters:
                del cls._filters[name]
                # Invalidate cache when filters change
                cls._sorted_filters_cache = None
                logger.debug(f"Unregistered display filter: {name}")

    @classmethod
    def apply_filters(cls, content: str, message_type: "MessageType") -> str:
        """Apply all registered filters to content.

        Args:
            content: Original message content.
            message_type: Type of message being displayed.

        Returns:
            Transformed content after all applicable filters.
        """
        with cls._lock:
            if not cls._filters:
                return content

            # Build cache if needed (only when filters change, not on every call)
            if cls._sorted_filters_cache is None:
                cls._sorted_filters_cache = sorted(
                    cls._filters.items(),
                    key=lambda x: x[1]["priority"],
                    reverse=True,
                )

            # Use cached sorted filters (defensive copy for iteration safety)
            sorted_filters = list(cls._sorted_filters_cache)

        # Apply filters outside the lock (filter functions may take time)
        for name, filter_info in sorted_filters:
            # Check if filter applies to this message type
            allowed_types = filter_info["message_types"]
            if allowed_types is not None and message_type not in allowed_types:
                continue

            try:
                content = filter_info["fn"](content, message_type)
            except Exception as e:
                logger.error(f"Display filter '{name}' failed: {e}")

        return content

    @classmethod
    def clear(cls) -> None:
        """Clear all registered filters (useful for testing)."""
        with cls._lock:
            cls._filters.clear()
            # Invalidate cache when filters change
            cls._sorted_filters_cache = None


class MessageType(Enum):
    """Types of messages in conversation."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    ERROR = "error"
    INFO = "info"
    DEBUG = "debug"


class MessageFormat(Enum):
    """Message formatting styles.

    Available for plugins to specify message display formatting.
    """

    PLAIN = "plain"
    GRADIENT = "gradient"
    HIGHLIGHTED = "highlighted"
    DIMMED = "dimmed"


# =============================================================================
# MODERN RENDERING METHODS (using design system)
# =============================================================================

# Width calculation for modern UI - all elements use global width
# MODERN_WIDTH removed - use get_global_width() instead
# Global width auto-calculates from terminal width via config
INDENT = ""  # No indent - TagBox handles layout


class ModernMessageRenderer:
    """Modern message renderer using design system (TagBox, Box, T, S).

    Implements the MessageRendererProtocol with full gradient-based
    rendering using TagBox for two-column layout (tag + content).
    """

    def user_message(self, content: str, width: int | None = None) -> str:
        """Render user message as a compact transcript row.

        Args:
            content: User message text (may contain newlines)
            width: Total width of the message (defaults to global width)

        Returns:
            Rendered user message as string
        """
        # Use global width if not specified
        if width is None:
            width = get_global_width()

        return self._compact_lines(
            content.split("\n"),
            width=width,
            text_color=T().text,
            first_prefix="▌ ",
            prefix_color=T().user_tag,
        )

    def assistant_message(self, content: str, width: int | None = None) -> str:
        """Render assistant response as compact plain text.

        Args:
            content: Response text (may contain newlines)
            width: Total width of the message (defaults to global width)

        Returns:
            Rendered response as string
        """
        # Use global width if not specified
        if width is None:
            width = get_global_width()

        return self.response_block(content.split("\n"), width=width)

    def response_block(self, lines: List[str], width: int | None = None) -> str:
        """Render multi-line LLM response as compact plain text.

        Args:
            lines: List of response text lines
            width: Total width of the message

        Returns:
            Rendered response block as string
        """
        # Use global width if not specified
        if width is None:
            width = get_global_width()

        return self._compact_lines(lines, width=width, text_color=T().text)

    def tool_call(
        self,
        name: str,
        args: str,
        status: str = "running",
        nested_width: int | None = None,
        result_summary: str | None = None,
    ) -> str:
        """Render tool call block with themed tag + colored gradient content.

        Args:
            name: Tool function name
            args: Tool arguments string
            status: Status - 'running', 'success', or 'error'
            nested_width: Width for nested content
            result_summary: Optional result summary to display (e.g., "Read 13 lines")

        Returns:
            Rendered tool call as string
        """
        # Use global width if not specified
        if nested_width is None:
            nested_width = get_global_width()

        label = name.strip().lower() or "tool"
        detail = args.strip()
        headline = self._truncate_plain(f"{label} {detail}".strip(), nested_width)

        status_colors = {
            "running": T().warning[0],
            "success": T().primary[0],
            "error": T().error[0],
        }
        label_color = status_colors.get(status, T().tool_tag)
        if headline == label:
            rendered = solid_fg(headline, label_color)
        elif headline.startswith(f"{label} "):
            rendered = (
                solid_fg(label, label_color)
                + " "
                + solid_fg(headline[len(label) + 1 :], T().text)
            )
        else:
            rendered = solid_fg(headline, T().text)

        display_text = result_summary
        if display_text is None and status == "running":
            display_text = "running..."
        elif display_text is None and status == "error":
            display_text = "error"

        if display_text:
            summary = self._truncate_plain(f"  ↳ {display_text}", nested_width)
            rendered += "\n" + solid_fg(summary, T().text_dim)

        return rendered

    @staticmethod
    def _truncate_plain(text: str, width: int) -> str:
        """Truncate plain text to a visible width."""
        if len(text) <= width:
            return text
        if width <= 1:
            return text[:width]
        return text[: width - 1] + "…"

    @staticmethod
    def _paint_compact_line(
        prefix: str,
        text: str,
        text_color: tuple[int, int, int],
        prefix_color: tuple[int, int, int] | None = None,
    ) -> str:
        rendered = ""
        if prefix:
            rendered += solid_fg(prefix, prefix_color or text_color)
        if text:
            rendered += solid_fg(text, text_color)
        return rendered

    def _compact_lines(
        self,
        lines: List[str],
        width: int,
        text_color: tuple[int, int, int],
        first_prefix: str = "",
        prefix_color: tuple[int, int, int] | None = None,
        continuation_prefix: str | None = None,
    ) -> str:
        """Render lines without full-width boxes or background fills."""
        if continuation_prefix is None:
            continuation_prefix = " " * len(first_prefix)

        rendered_lines: list[str] = []
        for line_idx, line in enumerate(lines):
            prefix = first_prefix if line_idx == 0 else continuation_prefix
            available = max(1, width - len(prefix))

            if line == "":
                rendered_lines.append(
                    self._paint_compact_line(prefix, "", text_color, prefix_color)
                    if prefix and line_idx == 0
                    else ""
                )
                continue

            wrapped_lines = wrap_text(line, available, word_wrap=True)
            for wrap_idx, wrapped in enumerate(wrapped_lines):
                active_prefix = prefix if wrap_idx == 0 else " " * len(prefix)
                rendered_lines.append(
                    self._paint_compact_line(
                        active_prefix,
                        wrapped,
                        text_color,
                        prefix_color if line_idx == 0 and wrap_idx == 0 else None,
                    )
                )

        return "\n".join(rendered_lines)

    def tool_result(
        self, lines: List[str], nested_width: int | None = None, wrap: bool = False
    ) -> str:
        """Render tool result block - nested, solid (no gradient).

        Args:
            lines: Result text lines
            nested_width: Width for nested content
            wrap: If True, wrap long lines to fit width

        Returns:
            Rendered tool result as string
        """
        # Use global width if not specified
        if nested_width is None:
            nested_width = get_global_width()

        code_bg = T().code_bg
        padded = [f"  {line}" for line in lines]
        # Preserve code formatting by default, enable wrapping for capture
        box = Box.render_solid(
            padded, code_bg, T().text_dim, nested_width, disable_wrapping=not wrap
        )
        return "\n".join(INDENT + line for line in box.split("\n"))

    def code_block(
        self,
        code_lines: List[str],
        lang: str = "python",
        nested_width: int | None = None,
    ) -> str:
        """Render code block with language-colored tag style.

        Args:
            code_lines: List of code lines
            lang: Programming language name
            nested_width: Width for nested content

        Returns:
            Rendered code block as string
        """
        # Use global width if not specified
        if nested_width is None:
            nested_width = get_global_width()

        # Language-specific tag colors
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

        lines = [f" {S.BOLD}{lang}{S.RESET_BOLD}"] + [f" {line}" for line in code_lines]
        tag_chars = [" # "] + ["   "] * len(code_lines)

        return TagBox.render(
            lines=lines,
            tag_bg=lang_bg,
            tag_fg=lang_fg,
            tag_width=3,
            content_colors=T().code_bg,
            content_fg=T().text,
            content_width=nested_width - 3,
            tag_chars=tag_chars,
            use_gradient=False,
            indent=INDENT,
            disable_wrapping=True,  # Preserve code formatting
        )

    def error_block(
        self, title: str, message: str, nested_width: int | None = None
    ) -> str:
        """Render error block as compact status text.

        Args:
            title: Error title
            message: Error message (may contain newlines)
            nested_width: Width for nested content

        Returns:
            Rendered error block as string
        """
        # Use global width if not specified
        if nested_width is None:
            nested_width = get_global_width()

        return self._compact_lines(
            [title] + message.split("\n"),
            width=nested_width,
            text_color=T().text,
            first_prefix="✖ ",
            prefix_color=T().error[0],
        )

    def warning_block(self, message: str, nested_width: int | None = None) -> str:
        """Render warning block as compact status text.

        Args:
            message: Warning message (may contain newlines)
            nested_width: Width for nested content

        Returns:
            Rendered warning block as string
        """
        # Use global width if not specified
        if nested_width is None:
            nested_width = get_global_width()

        return self._compact_lines(
            message.split("\n"),
            width=nested_width,
            text_color=T().text,
            first_prefix="⚠ ",
            prefix_color=T().warning[0],
        )

    def thinking_indicator(
        self, seconds: float, tokens: Optional[int] = None, width: int | None = None
    ) -> str:
        """Render thinking indicator with themed tag style.

        Args:
            seconds: Thinking time in seconds
            tokens: Optional token count
            width: Total width of the indicator

        Returns:
            Rendered thinking indicator as string
        """
        # Use global width if not specified
        if width is None:
            width = get_global_width()

        text = (
            f" thinking {seconds}s  ({tokens} tokens)"
            if tokens
            else f" thinking {seconds}s..."
        )
        return self._compact_lines(
            [text.strip()],
            width=width,
            text_color=T().text_dim,
            first_prefix="~ ",
            prefix_color=T().thinking_tag,
        )

    def info_block(self, message: str, width: int | None = None) -> str:
        """Render info block as compact status text.

        Args:
            message: Info message (may contain newlines)
            width: Total width of the block

        Returns:
            Rendered info block as string
        """
        # Use global width if not specified
        if width is None:
            width = get_global_width()

        return self._compact_lines(
            message.split("\n"),
            width=width,
            text_color=T().text,
            first_prefix="ℹ ",
            prefix_color=T().text_dim,
        )

    def success_block(self, message: str, width: int | None = None) -> str:
        """Render success block as compact status text.

        Args:
            message: Success message (may contain newlines)
            width: Total width of the block

        Returns:
            Rendered success block as string
        """
        # Use global width if not specified
        if width is None:
            width = get_global_width()

        return self._compact_lines(
            message.split("\n"),
            width=width,
            text_color=T().text,
            first_prefix="✔ ",
            prefix_color=T().success[0],
        )

    def agent_message(
        self,
        content: str,
        agent_color: tuple[int, int, int] | None = None,
        tag_char: str = " > ",
        observing: bool = False,
        width: int | None = None,
    ) -> str:
        """Render hub agent message with agent-specific tag color.

        Args:
            content: Message content (first line is header, rest is body)
            agent_color: RGB tuple for the agent's tag color
            tag_char: Tag indicator (" > " for direct, " ~ " for observing)
            observing: If True, dims the content for non-targeted messages
            width: Total width (defaults to global width)

        Returns:
            Rendered agent message as string
        """
        if width is None:
            width = get_global_width()

        if agent_color is None:
            agent_color = T().secondary[0]

        if observing:
            if agent_color is not None:
                agent_color = (
                    max(agent_color[0] // 3, 15),
                    max(agent_color[1] // 3, 15),
                    max(agent_color[2] // 3, 15),
                )
            fg_color = T().text_dim
        else:
            fg_color = T().text

        return self._compact_lines(
            content.split("\n"),
            width=width,
            text_color=fg_color,
            first_prefix=f"{tag_char.strip()} ",
            prefix_color=agent_color if isinstance(agent_color, tuple) else T().text_dim,
        )


class MessageRenderer:
    """Minimal message renderer for SDK compatibility.

    This class exists primarily for backward compatibility with the SDK.
    Actual message rendering is handled by MessageDisplayCoordinator using
    the active renderer instance.
    """

    def __init__(self, terminal_state, visual_effects=None):
        """Initialize message renderer.

        Args:
            terminal_state: TerminalState instance.
            visual_effects: VisualEffects instance (unused, kept for compatibility).
        """
        self.terminal_state = terminal_state
        self.visual_effects = visual_effects
        # Mode flags for compatibility with application.py
        self.simple_mode = False
        self.pipe_mode = False
        # conversation_renderer attribute for compatibility
        self.conversation_renderer = self
