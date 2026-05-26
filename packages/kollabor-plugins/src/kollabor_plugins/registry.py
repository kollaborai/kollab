"""Plugin registry system for Kollab."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Sequence, Type

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

    def __init__(
        self,
        plugins_dir: Path,
        extra_plugin_dirs: Sequence[Path] | None = None,
    ) -> None:
        """Initialize the plugin registry with specialized components.

        Args:
            plugins_dir: Directory containing plugin modules.
            extra_plugin_dirs: Additional user/project plugin roots.
        """
        self.plugins_dir = plugins_dir
        self.plugin_dirs = self._resolve_plugin_dirs(plugins_dir, extra_plugin_dirs)
        self.discoveries = [
            PluginDiscovery(plugin_dir) for plugin_dir in self.plugin_dirs
        ]
        self.discovery = self.discoveries[0]
        self.factory = PluginFactory()
        self.collector = PluginStatusCollector()
        logger.info(
            "Plugin registry initialized with specialized components: "
            f"{', '.join(str(plugin_dir) for plugin_dir in self.plugin_dirs)}"
        )

    @staticmethod
    def _resolve_plugin_dirs(
        plugins_dir: Path,
        extra_plugin_dirs: Sequence[Path] | None = None,
    ) -> List[Path]:
        candidates = [
            plugins_dir,
            *(extra_plugin_dirs or []),
            Path.home() / ".kollab" / "plugins",
            Path.cwd() / ".kollab" / "plugins",
        ]

        resolved_dirs: list[Path] = []
        seen: set[Path] = set()
        for candidate in candidates:
            resolved = candidate.expanduser().resolve()
            if resolved in seen:
                continue
            if candidate == plugins_dir or resolved.exists():
                resolved_dirs.append(candidate)
                seen.add(resolved)

        return resolved_dirs

    def _merge_discovery_results(self) -> None:
        primary = self.discovery

        for discovery in self.discoveries[1:]:
            for module_name in discovery.discovered_modules:
                if module_name not in primary.discovered_modules:
                    primary.discovered_modules.append(module_name)
            primary.loaded_classes.update(discovery.loaded_classes)
            primary.plugin_configs.update(discovery.plugin_configs)

    def discover_plugins(self) -> List[str]:
        """Discover available plugins in the plugins directory.

        Returns:
            List of discovered plugin module names.
        """
        discovered: list[str] = []
        for discovery in self.discoveries:
            discovered.extend(discovery.scan_plugin_files())
        self._merge_discovery_results()
        return discovered

    def load_plugin(self, module_name: str) -> None:
        """Load a plugin module and register its configuration.

        Args:
            module_name: Name of the plugin module to load.
        """
        self.discovery.load_module(module_name)

    def load_all_plugins(self) -> None:
        """Discover and load all available plugins."""
        for discovery in self.discoveries:
            discovery.discover_and_load()
        self._merge_discovery_results()
        logger.info(
            f"Plugin registry loaded {len(self.discovery.loaded_classes)} plugins"
        )

    def discover_classes_only(self) -> List[Type]:
        """Discover plugin classes across all configured roots."""
        plugin_classes: list[Type] = []

        for discovery in self.discoveries:
            for plugin_class in discovery.discover_classes_only():
                if plugin_class not in plugin_classes:
                    plugin_classes.append(plugin_class)

        self._merge_discovery_results()
        return plugin_classes

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
            "plugin_directories": [str(plugin_dir) for plugin_dir in self.plugin_dirs],
            "discovery_stats": self.discovery.get_discovery_stats(),
            "factory_stats": self.factory.get_factory_stats(),
            "collector_stats": self.collector.get_collector_stats(),
        }
