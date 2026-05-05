"""Status widget registry for the Kollab status area.

Provides a central registry for status widgets that can be displayed
in the configurable status area. Widgets can be registered by core
components or plugins.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class WidgetCategory(Enum):
    """Category for organizing widgets in the picker."""

    CORE = "core"
    PLUGIN = "plugin"


class WidthType(Enum):
    """Type of width specification for a widget."""

    AUTO = "auto"  # Widget determines its own width
    RELATIVE = "relative"  # Percentage of row width
    FIXED = "fixed"  # Fixed character count


@dataclass
class WidgetWidth:
    """Width specification for a widget.

    Attributes:
        type: How the width is calculated (auto, relative, fixed)
        value: The width value (percentage for relative, chars for fixed)
    """

    type: WidthType = WidthType.AUTO
    value: Optional[int] = None

    @classmethod
    def auto(cls) -> "WidgetWidth":
        """Create an auto-width specification."""
        return cls(type=WidthType.AUTO)

    @classmethod
    def relative(cls, percent: int) -> "WidgetWidth":
        """Create a relative (percentage) width specification."""
        return cls(type=WidthType.RELATIVE, value=max(1, min(100, percent)))

    @classmethod
    def fixed(cls, chars: int) -> "WidgetWidth":
        """Create a fixed (character count) width specification."""
        return cls(type=WidthType.FIXED, value=max(1, chars))

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result: Dict[str, Any] = {"type": self.type.value}
        if self.value is not None:
            result["value"] = self.value
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WidgetWidth":
        """Create from dictionary."""
        width_type = WidthType(data.get("type", "auto"))
        value = data.get("value")
        return cls(type=width_type, value=value)


@dataclass
class StatusWidget:
    """Definition of a status widget.

    Attributes:
        id: Unique identifier for the widget
        name: Display name for the widget picker
        description: Brief description of what the widget shows
        render_fn: Function that renders the widget content
        category: Core or plugin category
        default_width: Default width specification
        min_width: Minimum width in characters
        configurable: Whether widget has configuration options
        config_fn: Optional function to render configuration UI
        interactive: Whether widget supports user interaction
        interaction_type: Type of interaction ("modal", "toggle", "inline_edit", "action")
        on_activate: Optional async handler called when widget is activated
        edit_config: Optional configuration for inline edit widgets
        actions: Optional list of actions for action-type widgets
        states: Optional list of states for toggle widgets
    """

    id: str
    name: str
    description: str
    render_fn: Callable[[int, Any], str]  # (width, context) -> rendered string
    category: WidgetCategory = WidgetCategory.CORE
    default_width: WidgetWidth = field(default_factory=WidgetWidth.auto)
    min_width: int = 5
    configurable: bool = False
    config_fn: Optional[Callable[[], Dict[str, Any]]] = None
    # Interactive widget properties
    interactive: bool = False
    interaction_type: Optional[str] = (
        None  # "command", "toggle", "inline_edit", "action"
    )
    command: Optional[str] = None  # Slash command to execute (e.g., "/profile")
    on_activate: Optional[Callable[[str, Any], Any]] = (
        None  # (widget_id, context) -> result
    )
    edit_config: Optional[Dict[str, Any]] = None  # For inline_edit widgets
    actions: Optional[List[Dict[str, Any]]] = None  # For action widgets
    states: Optional[List[str]] = None  # For toggle widgets

    def render(self, width: int, context: Any = None) -> str:
        """Render the widget content.

        Args:
            width: Available width in characters
            context: Optional context object with services (llm_service, etc.)

        Returns:
            Rendered widget content as a string (may include ANSI codes)
        """
        try:
            return self.render_fn(width, context)
        except Exception as e:
            logger.error(f"Error rendering widget '{self.id}': {e}")
            return f"[{self.id}:err]"


class StatusWidgetRegistry:
    """Central registry for status widgets.

    Manages registration and lookup of status widgets from core
    components and plugins. Provides methods for the status setup
    UI to list available widgets.
    """

    def __init__(self):
        """Initialize the widget registry."""
        self._widgets: Dict[str, StatusWidget] = {}
        self._context: Any = None
        logger.info("StatusWidgetRegistry initialized")

    def set_context(self, context: Any) -> None:
        """Set the context object for widget rendering.

        Args:
            context: Object containing services (llm_service, profile_manager, etc.)
        """
        self._context = context

    def register(
        self,
        id: str,
        name: str,
        description: str,
        render_fn: Callable[[int, Any], str],
        category: Union[WidgetCategory, str] = WidgetCategory.CORE,
        default_width: Optional[Union[WidgetWidth, str, Dict]] = None,
        min_width: int = 5,
        configurable: bool = False,
        config_fn: Optional[Callable[[], Dict[str, Any]]] = None,
        # Interactive widget properties
        interactive: bool = False,
        interaction_type: Optional[str] = None,
        command: Optional[str] = None,
        on_activate: Optional[Callable[[str, Any], Any]] = None,
        edit_config: Optional[Dict[str, Any]] = None,
        actions: Optional[List[Dict[str, Any]]] = None,
        states: Optional[List[str]] = None,
    ) -> None:
        """Register a status widget.

        Args:
            id: Unique identifier for the widget
            name: Display name for the widget picker
            description: Brief description of what the widget shows
            render_fn: Function(width, context) -> str that renders the content
            category: Core or plugin category
            default_width: Default width (WidgetWidth, "auto", "25%", "20ch", or dict)
            min_width: Minimum width in characters
            configurable: Whether widget has configuration options
            config_fn: Optional function to render configuration UI
            interactive: Whether widget supports user interaction
            interaction_type: Type of interaction ("command", "toggle", "inline_edit", "action")
            command: Slash command to execute when activated (e.g., "/profile")
            on_activate: Optional async handler called when widget is activated
            edit_config: Optional configuration for inline edit widgets
            actions: Optional list of actions for action-type widgets
            states: Optional list of states for toggle widgets
        """
        # Convert category string to enum
        if isinstance(category, str):
            category = WidgetCategory(category)

        # Convert default_width to WidgetWidth
        if default_width is None:
            default_width = WidgetWidth.auto()
        elif isinstance(default_width, str):
            default_width = self._parse_width_string(default_width)
        elif isinstance(default_width, dict):
            default_width = WidgetWidth.from_dict(default_width)

        widget = StatusWidget(
            id=id,
            name=name,
            description=description,
            render_fn=render_fn,
            category=category,
            default_width=default_width,
            min_width=min_width,
            configurable=configurable,
            config_fn=config_fn,
            interactive=interactive,
            interaction_type=interaction_type,
            command=command,
            on_activate=on_activate,
            edit_config=edit_config,
            actions=actions,
            states=states,
        )

        self._widgets[id] = widget
        logger.info(f"Registered status widget: {id} ({name})")

    def _parse_width_string(self, width_str: str) -> WidgetWidth:
        """Parse a width string like 'auto', '25%', or '20ch'.

        Args:
            width_str: Width specification string

        Returns:
            WidgetWidth instance
        """
        width_str = width_str.strip().lower()

        if width_str == "auto":
            return WidgetWidth.auto()
        elif width_str.endswith("%"):
            try:
                percent = int(width_str[:-1])
                return WidgetWidth.relative(percent)
            except ValueError:
                logger.warning(f"Invalid percentage width: {width_str}, using auto")
                return WidgetWidth.auto()
        elif width_str.endswith("ch"):
            try:
                chars = int(width_str[:-2])
                return WidgetWidth.fixed(chars)
            except ValueError:
                logger.warning(f"Invalid fixed width: {width_str}, using auto")
                return WidgetWidth.auto()
        else:
            # Try parsing as integer (characters)
            try:
                chars = int(width_str)
                return WidgetWidth.fixed(chars)
            except ValueError:
                logger.warning(f"Unknown width format: {width_str}, using auto")
                return WidgetWidth.auto()

    def unregister(self, id: str) -> bool:
        """Unregister a widget.

        Args:
            id: Widget identifier to remove

        Returns:
            True if widget was removed, False if not found
        """
        if id in self._widgets:
            del self._widgets[id]
            logger.info(f"Unregistered status widget: {id}")
            return True
        return False

    def get(self, id: str) -> Optional[StatusWidget]:
        """Get a widget by ID.

        Args:
            id: Widget identifier

        Returns:
            StatusWidget if found, None otherwise
        """
        return self._widgets.get(id)

    def get_all(self) -> List[StatusWidget]:
        """Get all registered widgets.

        Returns:
            List of all registered widgets
        """
        return list(self._widgets.values())

    def get_by_category(self, category: WidgetCategory) -> List[StatusWidget]:
        """Get widgets by category.

        Args:
            category: Widget category to filter by

        Returns:
            List of widgets in the specified category
        """
        return [w for w in self._widgets.values() if w.category == category]

    def get_core_widgets(self) -> List[StatusWidget]:
        """Get all core widgets."""
        return self.get_by_category(WidgetCategory.CORE)

    def get_plugin_widgets(self) -> List[StatusWidget]:
        """Get all plugin widgets."""
        return self.get_by_category(WidgetCategory.PLUGIN)

    def render_widget(self, id: str, width: int) -> str:
        """Render a widget by ID.

        Args:
            id: Widget identifier
            width: Available width in characters

        Returns:
            Rendered widget content, or error placeholder if not found
        """
        widget = self.get(id)
        if widget:
            return widget.render(width, self._context)
        return f"[{id}:?]"

    def list_widget_ids(self) -> List[str]:
        """Get list of all widget IDs."""
        return list(self._widgets.keys())

    def widget_exists(self, id: str) -> bool:
        """Check if a widget exists."""
        return id in self._widgets
