"""Plugin API for registering status widgets.

Provides a simple interface for plugins to register their own
status widgets that users can add to the status area.
"""

import logging
from typing import Any, Callable, Union

from .widget_registry import (
    StatusWidgetRegistry,
    WidgetCategory,
    WidgetWidth,
)

logger = logging.getLogger(__name__)


class StatusWidgetAPI:
    """API for plugins to register status widgets.

    This class provides a simplified interface for plugins to register
    their widgets with the status system.

    Example usage in a plugin:

        class MyPlugin(BasePlugin):
            async def initialize(self):
                # Get the widget API from the app
                widget_api = self.app.get_widget_api()
                if widget_api:
                    widget_api.register_widget(
                        id="my-widget",
                        name="My Widget",
                        description="Shows custom info",
                        render_fn=self._render_widget,
                    )

            def _render_widget(self, width: int, context: Any) -> str:
                return f"My custom content"
    """

    def __init__(self, registry: StatusWidgetRegistry):
        """Initialize the widget API.

        Args:
            registry: The widget registry to register widgets with
        """
        self._registry = registry
        logger.info("StatusWidgetAPI initialized")

    def register_widget(
        self,
        id: str,
        name: str,
        description: str,
        render_fn: Callable[[int, Any], str],
        default_width: Union[str, WidgetWidth] = "auto",
        min_width: int = 5,
    ) -> bool:
        """Register a plugin widget.

        Args:
            id: Unique identifier for the widget (e.g., "my-plugin-widget")
            name: Display name shown in the widget picker
            description: Brief description of what the widget shows
            render_fn: Function that renders the widget content
                       Signature: (width: int, context: Any) -> str
                       The function receives the available width and a context
                       object with services like llm_service, profile_manager, etc.
            default_width: Default width - "auto", "25%", "20ch", or WidgetWidth
            min_width: Minimum width in characters

        Returns:
            True if registration succeeded, False otherwise

        Example render function:
            def render_my_widget(width: int, context: Any) -> str:
                # Access services from context if needed
                if context and hasattr(context, 'llm_service'):
                    msgs = context.llm_service.session_stats.get('messages', 0)
                    return f"Messages: {msgs}"
                return "My widget"
        """
        try:
            self._registry.register(
                id=id,
                name=name,
                description=description,
                render_fn=render_fn,
                category=WidgetCategory.PLUGIN,
                default_width=default_width,
                min_width=min_width,
            )
            logger.info(f"Plugin registered widget: {id}")
            return True
        except Exception as e:
            logger.error(f"Failed to register widget {id}: {e}")
            return False

    def unregister_widget(self, id: str) -> bool:
        """Unregister a plugin widget.

        Args:
            id: Widget identifier to remove

        Returns:
            True if removed, False if not found
        """
        return self._registry.unregister(id)

    def widget_exists(self, id: str) -> bool:
        """Check if a widget with the given ID exists.

        Args:
            id: Widget identifier

        Returns:
            True if widget exists
        """
        return self._registry.widget_exists(id)

    def get_plugin_widgets(self):
        """Get list of all registered plugin widgets.

        Returns:
            List of StatusWidget objects registered by plugins
        """
        return self._registry.get_plugin_widgets()
