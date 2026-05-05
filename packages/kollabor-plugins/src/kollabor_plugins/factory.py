"""Plugin factory for instantiating plugin classes with dependencies."""

import logging
from typing import Any, Dict, Type

from kollabor_events.error_utils import ErrorAccumulator, safe_execute

from .plugin_utils import has_method, instantiate_plugin_safely

logger = logging.getLogger(__name__)


class PluginFactory:
    """Handles plugin instantiation with dependency injection.

    This class is responsible for creating instances of plugin classes,
    managing their dependencies, and handling instantiation errors.
    """

    def __init__(self):
        """Initialize the plugin factory."""
        self.plugin_instances: Dict[str, Any] = {}
        self.instantiation_errors: Dict[str, str] = {}
        logger.info("PluginFactory initialized")

    def instantiate_plugin(
        self,
        plugin_class: Type,
        plugin_name: str,
        event_bus: Any,
        renderer: Any,
        config: Any,
    ) -> Any:
        """Instantiate a single plugin with dependencies.

        Args:
            plugin_class: The plugin class to instantiate.
            plugin_name: Name of the plugin.
            event_bus: Event bus for hook registration.
            renderer: Terminal renderer.
            config: Configuration manager.

        Returns:
            Plugin instance if successful, None otherwise.
        """
        # Check if the plugin class has an __init__ method
        if not has_method(plugin_class, "__init__"):
            logger.debug(
                f"Plugin {plugin_name} is not instantiable (no __init__ method)"
            )
            return None

        # Try to instantiate the plugin
        # Clean plugin name: remove 'Plugin' suffix if present and use as name
        clean_name = plugin_name
        if plugin_name.endswith("Plugin"):
            clean_name = plugin_name[:-6].lower()

        instance = instantiate_plugin_safely(
            plugin_class,
            name=clean_name,
            event_bus=event_bus,
            renderer=renderer,
            config=config,
        )

        if instance:
            # Store with both the class name and the clean name for compatibility
            self.plugin_instances[plugin_name] = instance
            self.plugin_instances[clean_name] = instance
            logger.info(
                f"Successfully instantiated plugin: {plugin_name} (as '{clean_name}')"
            )
        else:
            self.instantiation_errors[plugin_name] = "Failed to instantiate"
            logger.warning(f"Failed to instantiate plugin: {plugin_name}")

        return instance

    def instantiate_all(
        self,
        plugin_classes: Dict[str, Type],
        event_bus: Any,
        renderer: Any,
        config: Any,
    ) -> Dict[str, Any]:
        """Instantiate all provided plugin classes.

        Args:
            plugin_classes: Dictionary mapping plugin names to classes.
            event_bus: Event bus for hook registration.
            renderer: Terminal renderer.
            config: Configuration manager.

        Returns:
            Dictionary mapping plugin names to their instances.
        """
        error_accumulator = ErrorAccumulator(logger)

        for plugin_name, plugin_class in plugin_classes.items():
            instance = self.instantiate_plugin(
                plugin_class, plugin_name, event_bus, renderer, config
            )

            if not instance:
                error_accumulator.add_warning(
                    f"instantiating plugin {plugin_name}", "Plugin instantiation failed"
                )

        error_accumulator.report_summary()
        logger.info(
            f"Instantiated {len(self.plugin_instances)} plugins out of {len(plugin_classes)}"
        )

        return self.plugin_instances

    def get_instance(self, plugin_name: str) -> Any:
        """Get a plugin instance by name.

        Args:
            plugin_name: Name of the plugin.

        Returns:
            Plugin instance if found, None otherwise.
        """
        return self.plugin_instances.get(plugin_name)

    def get_all_instances(self) -> Dict[str, Any]:
        """Get all plugin instances.

        Returns:
            Dictionary mapping plugin names to instances.
        """
        return self.plugin_instances.copy()

    def get_instantiation_errors(self) -> Dict[str, str]:
        """Get errors from failed instantiations.

        Returns:
            Dictionary mapping plugin names to error messages.
        """
        return self.instantiation_errors.copy()

    def initialize_plugin(self, plugin_name: str) -> bool:
        """Initialize a plugin instance if it has an initialize method.

        Args:
            plugin_name: Name of the plugin to initialize.

        Returns:
            True if initialization successful, False otherwise.
        """
        instance = self.plugin_instances.get(plugin_name)
        if not instance:
            logger.warning(f"Cannot initialize non-existent plugin: {plugin_name}")
            return False

        if not has_method(instance, "initialize"):
            logger.debug(f"Plugin {plugin_name} has no initialize method")
            return True

        def _initialize():
            return instance.initialize()

        result = safe_execute(
            _initialize,
            f"initializing plugin {plugin_name}",
            default=False,
            logger_instance=logger,
        )

        return result is not False

    def initialize_all_plugins(self) -> Dict[str, bool]:
        """Initialize all plugin instances.

        Returns:
            Dictionary mapping plugin names to initialization success status.
        """
        initialization_results = {}

        for plugin_name in self.plugin_instances:
            success = self.initialize_plugin(plugin_name)
            initialization_results[plugin_name] = success

            if not success:
                logger.warning(f"Failed to initialize plugin: {plugin_name}")

        successful = sum(1 for s in initialization_results.values() if s)
        logger.info(f"Initialized {successful}/{len(initialization_results)} plugins")

        return initialization_results

    def shutdown_plugin(self, plugin_name: str) -> bool:
        """Shutdown a plugin instance if it has a shutdown method.

        Args:
            plugin_name: Name of the plugin to shutdown.

        Returns:
            True if shutdown successful, False otherwise.
        """
        instance = self.plugin_instances.get(plugin_name)
        if not instance:
            logger.warning(f"Cannot shutdown non-existent plugin: {plugin_name}")
            return False

        if not has_method(instance, "shutdown"):
            logger.debug(f"Plugin {plugin_name} has no shutdown method")
            return True

        def _shutdown():
            return instance.shutdown()

        result = safe_execute(
            _shutdown,
            f"shutting down plugin {plugin_name}",
            default=False,
            logger_instance=logger,
        )

        return result is not False

    def shutdown_all_plugins(self) -> Dict[str, bool]:
        """Shutdown all plugin instances.

        Returns:
            Dictionary mapping plugin names to shutdown success status.
        """
        shutdown_results = {}

        for plugin_name in self.plugin_instances:
            success = self.shutdown_plugin(plugin_name)
            shutdown_results[plugin_name] = success

            if not success:
                logger.warning(f"Failed to shutdown plugin: {plugin_name}")

        successful = sum(1 for s in shutdown_results.values() if s)
        logger.info(f"Shutdown {successful}/{len(shutdown_results)} plugins")

        return shutdown_results

    def get_factory_stats(self) -> Dict[str, Any]:
        """Get statistics about the factory's operations.

        Returns:
            Dictionary with factory statistics.
        """
        return {
            "total_instances": len(self.plugin_instances),
            "instantiation_errors": len(self.instantiation_errors),
            "plugin_names": list(self.plugin_instances.keys()),
            "error_plugins": list(self.instantiation_errors.keys()),
            "instance_types": {
                name: type(instance).__name__
                for name, instance in self.plugin_instances.items()
            },
        }
