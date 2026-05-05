"""Base widget class for modal UI components."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple, Union

from kollabor_tui.design_system import T, TagBox
from kollabor_tui.key_parser import KeyPress


@dataclass
class WidgetStyle:
    """Styling configuration for widget rendering.

    Attributes:
        tag_bg: RGB tuple for tag background color
        tag_fg: RGB tuple for tag foreground color
        tag_width: Width of the tag column
        content_colors: List of RGB tuples for gradient, or single RGB for solid
        content_fg: RGB tuple for content foreground color
        content_width: Width of the content column
        use_gradient: True for gradient background, False for solid
    """

    tag_bg: Tuple[int, int, int]
    tag_fg: Optional[Tuple[int, int, int]]
    tag_width: int
    content_colors: Union[Tuple[int, int, int], List[Tuple[int, int, int]]]
    content_fg: Tuple[int, int, int]
    content_width: int
    use_gradient: bool


class BaseWidget(ABC):
    """Base class for all modal widgets.

    Provides common functionality for rendering, input handling, and value management
    across all widget types including checkboxes, dropdowns, text inputs, and sliders.
    """

    def __init__(self, config: dict, config_path: str, config_service=None):
        """Initialize base widget.

        Args:
            config: Widget configuration dictionary containing display options.
            config_path: Dot-notation path to config value (e.g., "kollabor.llm.temperature").
            config_service: ConfigService instance for reading/writing config values.
        """
        self.config = config
        self.config_path = config_path
        self.config_service = config_service
        self.focused = False
        self._pending_value = None

    @abstractmethod
    def render(self) -> List[str]:
        """Render widget using design system styling.

        Returns:
            List of strings representing widget display lines.
        """
        pass

    @abstractmethod
    def handle_input(self, key_press: KeyPress) -> bool:
        """Handle input, return True if consumed.

        Args:
            key_press: Key press event to handle.

        Returns:
            True if the key press was handled by this widget.
        """
        pass

    def get_style(self, width: int = 50) -> WidgetStyle:
        """Get appropriate widget style based on focused state.

        Args:
            width: Total widget width (default: 50).

        Returns:
            WidgetStyle configuration for rendering.
        """
        tag_width = 3
        content_width = width - tag_width

        if self.focused:
            return WidgetStyle(
                tag_bg=T().primary[0],
                tag_fg=None,
                tag_width=tag_width,
                content_colors=T().input_bg,
                content_fg=T().text,
                content_width=content_width,
                use_gradient=True,
            )
        else:
            return WidgetStyle(
                tag_bg=T().dark[0],
                tag_fg=T().text_dim,
                tag_width=tag_width,
                content_colors=T().dark[0],
                content_fg=T().text_dim,
                content_width=content_width,
                use_gradient=False,
            )

    def render_modern(
        self,
        lines: List[str],
        tag_chars: Optional[List[str]] = None,
        width: int = 50,
        tag_bg: Optional[Tuple[int, int, int]] = None,
        tag_fg: Optional[Tuple[int, int, int]] = None,
        content_colors: Optional[
            Union[Tuple[int, int, int], List[Tuple[int, int, int]]]
        ] = None,
        content_fg: Optional[Tuple[int, int, int]] = None,
        position: str = "only",
    ) -> str:
        """Render widget using modern design system with TagBox.

        This method provides design system integration while maintaining backward
        compatibility with the existing render() method.

        Args:
            lines: List of content lines to display.
            tag_chars: List of tag characters for each line (default: all spaces).
            width: Total widget width (default: 50).
            tag_bg: Override tag background color (default: from get_style()).
            tag_fg: Override tag foreground color (default: from get_style()).
            content_colors: Override content colors (default: from get_style()).
            content_fg: Override content foreground (default: from get_style()).
            position: Border position - 'only' (both borders), 'first' (top only),
                     'middle' (no borders), 'last' (bottom only). Used for grouping
                     widgets within a section.

        Returns:
            Rendered widget string with TagBox styling.

        Example:
            # Use default styling based on focus state
            output = widget.render_modern(["Hello World"], [" ◆ "])

            # Override colors for specific widget type
            output = widget.render_modern(
                ["Error message"],
                [" x "],
                tag_bg=T().error[0],
                content_colors=T().error
            )

            # Group widgets - first in section
            output = widget.render_modern(["First item"], position="first")
        """
        style = self.get_style(width)

        # Allow overrides
        final_tag_bg = tag_bg if tag_bg is not None else style.tag_bg
        final_tag_fg = tag_fg if tag_fg is not None else style.tag_fg
        final_content_colors = (
            content_colors if content_colors is not None else style.content_colors
        )
        final_content_fg = content_fg if content_fg is not None else style.content_fg

        # Default tag chars: all spaces
        if tag_chars is None:
            tag_chars = ["   "] * len(lines)

        return TagBox.render(
            lines=lines,
            tag_bg=final_tag_bg,
            tag_fg=final_tag_fg,
            tag_width=style.tag_width,
            content_colors=final_content_colors,
            content_fg=final_content_fg,
            content_width=style.content_width,
            tag_chars=tag_chars,
            use_gradient=style.use_gradient,
            position=position,
        )

    def get_value(self) -> Any:
        """Get current value from config system.

        Returns:
            Current configuration value for this widget's config path.
        """
        # First check if widget config has an explicit 'value' or 'current_value' field
        # This is used for form modals with pre-populated data
        if "value" in self.config:
            return self.config["value"]
        if "current_value" in self.config:
            return self.config["current_value"]

        # Try to get real value from config service
        if self.config_service:
            try:
                value = self.config_service.get(self.config_path)
                # If we got a value, return it
                if value is not None:
                    return value
            except Exception:
                # Fall through to defaults if config access fails
                pass

        # Fallback to defaults for testing or when config service is unavailable
        # Use reasonable defaults based on widget type
        widget_type = self.__class__.__name__.lower()

        if "checkbox" in widget_type:
            return True
        elif "slider" in widget_type:
            # For sliders, check config for min/max and return middle value
            min_val = self.config.get("min_value", 0)
            max_val = self.config.get("max_value", 1)
            return (min_val + max_val) / 2
        elif "dropdown" in widget_type:
            options = self.config.get("options", [])
            return options[0] if options else "Unknown"
        elif "text_input" in widget_type:
            placeholder = self.config.get("placeholder", "")
            return placeholder
        else:
            return ""

    def set_value(self, value: Any):
        """Set value (will be saved in Phase 3).

        Args:
            value: New value to set for this widget.
        """
        self._pending_value = value

    def get_pending_value(self) -> Any:
        """Get pending value if set, otherwise current value.

        Returns:
            Pending value if available, otherwise current config value.
        """
        return (
            self._pending_value if self._pending_value is not None else self.get_value()
        )

    def has_pending_changes(self) -> bool:
        """Check if widget has unsaved changes.

        Returns:
            True if there are pending changes to save.
        """
        return self._pending_value is not None

    def set_focus(self, focused: bool):
        """Set widget focus state.

        Args:
            focused: Whether widget should be focused.
        """
        self.focused = focused

    def get_label(self) -> str:
        """Get widget label from config.

        Returns:
            Label text for display.
        """
        return str(self.config.get("label", "Widget"))

    def is_valid_value(self, value: Any) -> bool:
        """Validate if a value is acceptable for this widget.

        Args:
            value: Value to validate.

        Returns:
            True if value is valid for this widget type.
        """
        return True  # Base implementation accepts any value
