"""Label widget for read-only value display."""

from typing import Any, List

from kollabor_tui.design_system import Box, S, T, TagBox

from .base_widget import BaseWidget


class LabelWidget(BaseWidget):
    """Read-only label widget for displaying status values.

    Unlike other widgets, this doesn't allow user interaction -
    it simply displays a label and value pair.
    """

    def __init__(
        self,
        label: str,
        value: str = "",
        help_text: str = "",
        config_path: str = "",
        current_value: Any = None,
        **kwargs,
    ):
        """Initialize label widget.

        Args:
            label: Display label text.
            value: Value to display (can also be set via current_value).
            help_text: Optional help text.
            config_path: Config path (usually empty for labels).
            current_value: Alternative way to set value.
            **kwargs: Additional configuration.
        """
        config = {
            "label": label,
            "value": value or str(current_value or ""),
            "help": help_text,
            **kwargs,
        }
        super().__init__(config, config_path, None)
        self._value = value or str(current_value or "")

    def render(self) -> List[str]:
        """Render the label widget.

        Returns:
            List containing single label display line.
        """
        label = self.config.get("label", "")
        value = self._value

        # Format: "  Label: Value" (matching other widgets' indentation)
        if self.focused:
            rendered = f"{S.BOLD}  {label}: {value}{S.RESET_BOLD}"
        else:
            rendered = f"  {label}: {value}"

        return [rendered]

    def render_modern(  # type: ignore[override]
        self, style: str = "normal", width: int = 50, position: str = "only"
    ) -> List[str]:
        """Render the label widget with modern styling.

        Args:
            style: Rendering style ('normal', 'header', 'info', 'success', 'warning', 'error')
            width: Width of the rendered output
            position: Border position - 'only' (both), 'first' (top only),
                     'middle' (no borders), 'last' (bottom only).

        Returns:
            List containing modern styled label rendering.

        Style details:
            - normal: Simple TagBox with dark background
            - header: TagBox with primary tag, bold text, square icon
            - info: Full Box.render with secondary colors, info icon
            - success: Full Box.render with success colors, checkmark icon
            - warning: Full Box.render with warning colors, warning icon
            - error: Full Box.render with error colors, X icon
        """
        label = self.config.get("label", "")
        value = self._value
        text = f"{label}: {value}" if value else label
        tag_width = 3

        # Unicode characters for icons
        chars = {
            "square": "■",
            "info": "ℹ",
            "success": "✔",
            "warning": "⚠",
            "error": "✖",
        }

        # Normal/header use TagBox style
        if style == "header":
            rendered = TagBox.render(
                lines=[f" {S.BOLD}{text}{S.RESET_BOLD}"],
                tag_bg=T().primary[0],

                tag_width=tag_width,
                content_colors=T().dark[0],
                content_fg=T().text,
                content_width=width - tag_width,
                tag_chars=[f" {chars['square']} "],
                use_gradient=False,
                position=position,
            )
            return [rendered]
        elif style == "normal":
            rendered = TagBox.render(
                lines=[f" {text}"],
                tag_bg=T().dark[0],
                tag_fg=T().text_dim,
                tag_width=tag_width,
                content_colors=T().dark[0],
                content_fg=T().text_dim,
                content_width=width - tag_width,
                tag_chars=["   "],
                use_gradient=False,
                position=position,
            )
            return [rendered]

        # Semantic styles use full colored Box (like UI.warning, UI.error)
        style_config = {
            "info": (T().secondary, T().text, chars["info"]),
            "success": (T().success, T().text_dark, chars["success"]),
            "warning": (T().warning, T().text_dark, chars["warning"]),
            "error": (T().error, T().text, chars["error"]),
        }
        colors, fg, icon = style_config.get(style, (T().dark, T().text_dim, " "))

        return [Box.render([f"  {icon} {text}"], colors, fg, width)]

    def handle_input(self, key_press) -> bool:
        """Handle input (no-op for labels).

        Args:
            key_press: Key press event.

        Returns:
            False - labels don't consume input.
        """
        return False

    def get_value(self) -> str:
        """Get the label value.

        Returns:
            Current value string.
        """
        return self._value

    def set_value(self, value: Any) -> None:
        """Set the label value.

        Args:
            value: New value to display.
        """
        self._value = str(value)
