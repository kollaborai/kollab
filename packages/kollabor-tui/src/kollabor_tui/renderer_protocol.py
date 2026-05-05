"""Message renderer protocol for pluggable UI rendering.

Defines the interface that all message renderers must implement.
Renderers control how conversation messages (user, assistant, tools,
errors, etc.) are visually presented in the terminal.

Built-in implementations:
    - ModernMessageRenderer: Full TagBox + gradient rendering
    - CleanRenderer: Tag column colored, content plain (resize-safe)
    - SimpleRenderer: Plain text with unicode prefixes

Custom renderers can be created by implementing this protocol
and registering via config or plugin.
"""

import logging
from typing import Any, List, Optional, Protocol, Tuple, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class MessageRendererProtocol(Protocol):
    """Protocol defining the message renderer interface.

    All message renderers must implement these methods.
    Each method receives content and returns a rendered string
    ready for terminal output (may contain ANSI escape codes).
    """

    def user_message(self, content: str, width: Optional[int] = None) -> str:
        """Render a user message.

        Args:
            content: User message text (may contain newlines)
            width: Total width (defaults to global width)

        Returns:
            Rendered string for terminal output
        """
        ...

    def assistant_message(self, content: str, width: Optional[int] = None) -> str:
        """Render an assistant message.

        Args:
            content: Response text (may contain newlines)
            width: Total width (defaults to global width)

        Returns:
            Rendered string for terminal output
        """
        ...

    def response_block(self, lines: List[str], width: Optional[int] = None) -> str:
        """Render a multi-line assistant response block.

        Args:
            lines: List of response text lines
            width: Total width (defaults to global width)

        Returns:
            Rendered string for terminal output
        """
        ...

    def tool_call(
        self,
        name: str,
        args: str,
        status: str = "running",
        nested_width: Optional[int] = None,
        result_summary: Optional[str] = None,
    ) -> str:
        """Render a tool call block.

        Args:
            name: Tool function name
            args: Tool arguments string
            status: 'running', 'success', or 'error'
            nested_width: Width for nested content
            result_summary: Optional result summary

        Returns:
            Rendered string for terminal output
        """
        ...

    def tool_result(
        self, lines: List[str], nested_width: Optional[int] = None, wrap: bool = False
    ) -> str:
        """Render a tool result block.

        Args:
            lines: Result text lines
            nested_width: Width for nested content
            wrap: If True, wrap long lines

        Returns:
            Rendered string for terminal output
        """
        ...

    def code_block(
        self,
        code_lines: List[str],
        lang: str = "python",
        nested_width: Optional[int] = None,
    ) -> str:
        """Render a code block.

        Args:
            code_lines: List of code lines
            lang: Programming language name
            nested_width: Width for nested content

        Returns:
            Rendered string for terminal output
        """
        ...

    def error_block(
        self, title: str, message: str, nested_width: Optional[int] = None
    ) -> str:
        """Render an error block.

        Args:
            title: Error title
            message: Error message (may contain newlines)
            nested_width: Width for nested content

        Returns:
            Rendered string for terminal output
        """
        ...

    def warning_block(self, message: str, nested_width: Optional[int] = None) -> str:
        """Render a warning block.

        Args:
            message: Warning message (may contain newlines)
            nested_width: Width for nested content

        Returns:
            Rendered string for terminal output
        """
        ...

    def info_block(self, message: str, width: Optional[int] = None) -> str:
        """Render an info block.

        Args:
            message: Info message (may contain newlines)
            width: Total width (defaults to global width)

        Returns:
            Rendered string for terminal output
        """
        ...

    def success_block(self, message: str, width: Optional[int] = None) -> str:
        """Render a success block.

        Args:
            message: Success message (may contain newlines)
            width: Total width (defaults to global width)

        Returns:
            Rendered string for terminal output
        """
        ...

    def agent_message(
        self,
        content: str,
        agent_color: Optional[Tuple[Any, ...]] = None,
        tag_char: str = " > ",
        observing: bool = False,
        width: Optional[int] = None,
    ) -> str:
        """Render a hub agent message with agent-specific color.

        Args:
            content: Message content (first line is header)
            agent_color: RGB tuple for agent's tag color
            tag_char: Tag indicator (" > " direct, " ~ " observing)
            observing: If True, dims for non-targeted messages
            width: Total width (defaults to global width)

        Returns:
            Rendered string for terminal output
        """
        ...

    def thinking_indicator(
        self, seconds: float, tokens: Optional[int] = None, width: Optional[int] = None
    ) -> str:
        """Render a thinking indicator.

        Args:
            seconds: Thinking time in seconds
            tokens: Optional token count
            width: Total width (defaults to global width)

        Returns:
            Rendered string for terminal output
        """
        ...


def get_renderer(name: str = "modern") -> MessageRendererProtocol:
    """Factory function to get a renderer by name.

    Args:
        name: Renderer name - 'modern', 'clean', or 'simple'

    Returns:
        Renderer instance implementing MessageRendererProtocol

    Raises:
        ValueError: If renderer name is not recognized
    """
    if name == "modern":
        from kollabor_tui.message_renderer import ModernMessageRenderer

        return ModernMessageRenderer()  # type: ignore[return-value]
    elif name == "clean":
        from kollabor_tui.clean_renderer import CleanRenderer

        return CleanRenderer()  # type: ignore[return-value]
    elif name == "simple":
        from kollabor_tui.simple_renderer import SimpleRenderer

        return SimpleRenderer()  # type: ignore[return-value]
    else:
        logger.warning(f"Unknown renderer '{name}', falling back to modern")
        from kollabor_tui.message_renderer import ModernMessageRenderer

        return ModernMessageRenderer()  # type: ignore[return-value]
