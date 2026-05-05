"""Plugin collector for gathering plugin information."""

import logging
from typing import Any, Dict, List

from kollabor_events.error_utils import safe_execute

logger = logging.getLogger(__name__)


class PluginStatusCollector:
    """Collects plugin information like startup info.

    Note: Status line collection has been removed in favor of
    content provider-based status views.
    """

    def __init__(self):
        """Initialize the plugin collector."""
        logger.info("PluginStatusCollector initialized")

    def get_plugin_startup_info(
        self, plugin_name: str, plugin_class: type, config: Any
    ) -> List[str]:
        """Get startup information for a specific plugin.

        Args:
            plugin_name: Name of the plugin.
            plugin_class: The plugin class.
            config: Configuration manager instance.

        Returns:
            List of startup info strings, or empty list if no info available.
        """

        def get_startup_info():
            return plugin_class.get_startup_info(config)

        result: Any = safe_execute(
            get_startup_info,
            f"getting startup info from plugin {plugin_name}",
            default=[],
            logger_instance=logger,
        )

        return result if isinstance(result, list) else []

    def collect_all_startup_info(
        self, plugin_classes: Dict[str, type], config: Any
    ) -> Dict[str, List[str]]:
        """Collect startup information from all plugin classes.

        Args:
            plugin_classes: Dictionary mapping plugin names to classes.
            config: Configuration manager instance.

        Returns:
            Dictionary mapping plugin names to their startup info lists.
        """
        startup_info = {}

        for plugin_name, plugin_class in plugin_classes.items():
            info_list = self.get_plugin_startup_info(plugin_name, plugin_class, config)
            if info_list:
                startup_info[plugin_name] = info_list

        logger.debug(f"Collected startup info from {len(startup_info)} plugins")
        return startup_info

    def get_collector_stats(self) -> Dict[str, Any]:
        """Get statistics about the collector.

        Returns:
            Dictionary with collector statistics.
        """
        return {
            "type": "startup_info_collector",
            "description": "Collects plugin startup information",
        }
