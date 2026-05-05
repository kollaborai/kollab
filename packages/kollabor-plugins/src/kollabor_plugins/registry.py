"""Plugin registry system for Kollab."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Type

from kollabor_events.dict_utils import deep_merge

from .collector import PluginStatusCollector
from .discovery import PluginDiscovery
from .factory import PluginFactory

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Simplified registry coordinating plugin discovery, instantiation, and status collection.

    This class coordinates between three specialized components:
    - PluginDiscovery: File system scanning and module loading
    - PluginFactory: Plugin instantiation with dependencies
    - PluginStatusCollector: Status aggregation from plugin instances
    """

    def __init__(self, plugins_dir: Path) -> None:
        """Initialize the plugin registry with specialized components.

        Args:
            plugins_dir: Directory containing plugin modules.
        """
        self.plugins_dir = plugins_dir
        self.discovery = PluginDiscovery(plugins_dir)
        self.factory = PluginFactory()
        self.collector = PluginStatusCollector()
        logger.info(
            f"Plugin registry initialized with specialized components: {plugins_dir}"
        )

    def discover_plugins(self) -> List[str]:
        """Discover available plugins in the plugins directory.

        Returns:
            List of discovered plugin module names.
        """
        return self.discovery.scan_plugin_files()

    def load_plugin(self, module_name: str) -> None:
        """Load a plugin module and register its configuration.

        Args:
            module_name: Name of the plugin module to load.
        """
        self.discovery.load_module(module_name)

    def load_all_plugins(self) -> None:
        """Discover and load all available plugins."""
        self.discovery.discover_and_load()
        logger.info(
            f"Plugin registry loaded {len(self.discovery.loaded_classes)} plugins"
        )

    def get_merged_config(self) -> Dict[str, Any]:
        """Get merged configuration from all registered plugins.

        Returns:
            Merged configuration dictionary from all plugins.
        """
        merged_config: Dict[str, Any] = {}
        plugin_configs = self.discovery.get_all_configs()

        for plugin_name, plugin_config in plugin_configs.items():
            # Deep merge plugin config into merged_config
            merged_config = deep_merge(merged_config, plugin_config)
            logger.debug(f"Merged config from plugin: {plugin_name}")

        return merged_config

    def get_plugin_class(self, plugin_name: str) -> Type:
        """Get a registered plugin class by name.

        Args:
            plugin_name: Name of the plugin class.

        Returns:
            Plugin class if found.

        Raises:
            KeyError: If plugin is not registered.
        """
        return self.discovery.get_plugin_class(plugin_name)

    def get_plugin_startup_info(self, plugin_name: str, config) -> List[str]:
        """Get startup information for a plugin.

        Args:
            plugin_name: Name of the plugin class.
            config: Configuration manager instance.

        Returns:
            List of startup info strings, or empty list if no info available.
        """
        try:
            plugin_class = self.discovery.get_plugin_class(plugin_name)
            return self.collector.get_plugin_startup_info(
                plugin_name, plugin_class, config
            )
        except KeyError:
            logger.warning(f"Plugin {plugin_name} not found for startup info")
            return []

    def list_plugins(self) -> List[str]:
        """Get list of registered plugin names.

        Returns:
            List of registered plugin names.
        """
        return list(self.discovery.loaded_classes.keys())

    def instantiate_plugins(self, event_bus, renderer, config) -> Dict[str, Any]:
        """Create instances of all registered plugins that can be instantiated.

        Args:
            event_bus: Event bus for hook registration.
            renderer: Terminal renderer.
            config: Configuration manager.

        Returns:
            Dictionary mapping plugin names to their instances.
        """
        plugin_classes = self.discovery.loaded_classes
        return self.factory.instantiate_all(plugin_classes, event_bus, renderer, config)

    def get_registry_stats(self) -> Dict[str, Any]:
        """Get comprehensive statistics about the registry and its components.

        Returns:
            Dictionary with detailed registry statistics.
        """
        return {
            "plugins_directory": str(self.plugins_dir),
            "discovery_stats": self.discovery.get_discovery_stats(),
            "factory_stats": self.factory.get_factory_stats(),
            "collector_stats": self.collector.get_collector_stats(),
        }
