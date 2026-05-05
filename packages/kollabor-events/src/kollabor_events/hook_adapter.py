"""Hook adapter for backward compatibility with old hook format.

This module provides the HookAdapter class that manages plugin instance tracking
and format conversion between old and new hook formats.

Old format (pre-migration):
    Hook(name="hook_name", plugin=plugin_instance, ...)

New format (post-migration):
    Hook(name="hook_name", plugin_name="plugin_name", ...)

The adapter automatically detects the format and converts as needed, while
maintaining a plugin instance registry for execution.
"""

import logging
from typing import Any, Dict, Optional, Set

from .models import Hook

logger = logging.getLogger(__name__)


class HookAdapter:
    """Adapter for hook format migration and plugin instance tracking.

    This class provides backward compatibility for hooks created with the old
    format (plugin object reference) by tracking plugin instances and converting
    to the new format (plugin_name string) during registration.

    Responsibilities:
        1. Track plugin instances by name for reverse lookup
        2. Convert old hook format to new format during registration
        3. Provide plugin instance injection during hook execution
        4. Auto-detect hook format (old vs new)
        5. Maintain backward compatibility with both formats

    Thread Safety:
        Plugin instance tracking is read-only after registration, making it
        inherently thread-safe for hook execution scenarios.
    """

    def __init__(self):
        """Initialize the hook adapter."""
        # Track plugin instances by name: {"plugin_name": plugin_instance}
        self._plugin_instances: Dict[str, Any] = {}

        # Track registered plugin names for validation
        self._registered_plugins: Set[str] = set()

        # Statistics for migration monitoring
        self._stats = {
            "hooks_registered": 0,
            "hooks_converted": 0,
            "plugins_tracked": 0,
        }

        logger.info("HookAdapter initialized")

    def register_plugin(self, plugin_name: str, plugin_instance: Any) -> None:
        """Register a plugin instance for hook execution.

        Args:
            plugin_name: Name identifier for the plugin.
            plugin_instance: The actual plugin instance object.

        Raises:
            ValueError: If plugin_name is empty or plugin_instance is None.
        """
        if not plugin_name:
            raise ValueError("plugin_name cannot be empty")

        if plugin_instance is None:
            raise ValueError("plugin_instance cannot be None")

        # Track plugin instance
        self._plugin_instances[plugin_name] = plugin_instance
        self._registered_plugins.add(plugin_name)

        self._stats["plugins_tracked"] = len(self._plugin_instances)

        logger.debug(f"Registered plugin instance: {plugin_name}")

    def unregister_plugin(self, plugin_name: str) -> bool:
        """Unregister a plugin instance.

        Args:
            plugin_name: Name of the plugin to unregister.

        Returns:
            True if plugin was unregistered, False if not found.
        """
        if plugin_name in self._plugin_instances:
            del self._plugin_instances[plugin_name]
            self._registered_plugins.discard(plugin_name)
            self._stats["plugins_tracked"] = len(self._plugin_instances)
            logger.debug(f"Unregistered plugin instance: {plugin_name}")
            return True

        logger.warning(f"Plugin instance not found for unregistration: {plugin_name}")
        return False

    def get_plugin_instance(self, plugin_name: str) -> Optional[Any]:
        """Get a plugin instance by name.

        Args:
            plugin_name: Name of the plugin to retrieve.

        Returns:
            Plugin instance if found, None otherwise.
        """
        return self._plugin_instances.get(plugin_name)

    def adapt_hook_for_registration(self, hook: Any) -> Hook:
        """Adapt a hook to the new format for registration.

        Detects whether the hook uses the old format (plugin object reference)
        or new format (plugin_name string) and converts if necessary.

        Args:
            hook: Hook object (either old or new format).

        Returns:
            Hook in the new format with plugin_name string.

        Raises:
            ValueError: If hook cannot be adapted or plugin not registered.
        """
        # Check if hook is already in new format (has plugin_name attribute)
        if hasattr(hook, "plugin_name") and isinstance(hook.plugin_name, str):
            # New format - validate plugin is registered
            if hook.plugin_name not in self._registered_plugins:
                logger.warning(
                    f"Hook {hook.name} references unregistered plugin: {hook.plugin_name}. "
                    f"Ensure plugin is registered via register_plugin() before hook registration."
                )

            self._stats["hooks_registered"] += 1
            return hook  # type: ignore[no-any-return]

        # Check if hook is in old format (has plugin attribute with object reference)
        if hasattr(hook, "plugin") and not isinstance(hook.plugin, str):
            # Old format - convert to new format
            plugin_instance = hook.plugin

            # Try to get plugin name from instance
            plugin_name = self._get_plugin_name_from_instance(plugin_instance)

            if plugin_name is None:
                raise ValueError(
                    f"Cannot determine plugin name for hook {hook.name}. "
                    f"Ensure plugin instance has a 'name' attribute or is registered."
                )

            # Register plugin instance if not already tracked
            if plugin_name not in self._registered_plugins:
                self.register_plugin(plugin_name, plugin_instance)
                logger.debug(
                    f"Auto-registered plugin instance during hook conversion: {plugin_name}"
                )

            # Create new format hook
            adapted_hook = Hook(
                name=hook.name,
                plugin_name=plugin_name,
                event_type=hook.event_type,
                priority=hook.priority,
                callback=hook.callback,
                enabled=getattr(hook, "enabled", True),
                timeout=getattr(hook, "timeout", None),
                retry_attempts=getattr(hook, "retry_attempts", None),
                error_action=getattr(hook, "error_action", None),
            )

            self._stats["hooks_registered"] += 1
            self._stats["hooks_converted"] += 1

            logger.info(
                f"Converted hook from old format: {hook.name} "
                f"(plugin: {plugin_name}, event: {hook.event_type})"
            )

            return adapted_hook

        # Unknown format - use getattr to avoid AttributeError
        hook_name = getattr(hook, "name", "<unknown>")
        raise ValueError(
            f"Hook {hook_name} has neither 'plugin_name' (new format) "
            f"nor 'plugin' (old format) attribute. Cannot adapt."
        )

    def _get_plugin_name_from_instance(self, plugin_instance: Any) -> Optional[str]:
        """Extract plugin name from plugin instance.

        Args:
            plugin_instance: The plugin instance object.

        Returns:
            Plugin name if found, None otherwise.
        """
        # Try common name attributes
        for attr in ("name", "plugin_name", "_name", "__name__"):
            if hasattr(plugin_instance, attr):
                name = getattr(plugin_instance, attr)
                if isinstance(name, str) and name:
                    return name

        # Try to find registered instance by object identity
        for registered_name, registered_instance in self._plugin_instances.items():
            if registered_instance is plugin_instance:
                return registered_name

        return None

    def inject_plugin_instance_for_execution(self, hook: Hook) -> Dict[str, Any]:
        """Inject plugin instance into hook context for execution.

        Creates an execution context that includes both the hook and its
        associated plugin instance for use during hook callback execution.

        Args:
            hook: Hook in new format (with plugin_name).

        Returns:
            Dictionary with hook and plugin_instance for execution.

        Raises:
            ValueError: If plugin instance not found.
        """
        plugin_instance = self.get_plugin_instance(hook.plugin_name)

        if plugin_instance is None:
            raise ValueError(
                f"Plugin instance not found for hook {hook.name}: {hook.plugin_name}. "
                f"Ensure plugin is registered via register_plugin() before hook execution."
            )

        return {
            "hook": hook,
            "plugin_instance": plugin_instance,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get adapter statistics.

        Returns:
            Dictionary with adapter statistics.
        """
        return {
            **self._stats,
            "tracked_plugins": list(self._registered_plugins),
            "conversion_rate": (
                self._stats["hooks_converted"] / self._stats["hooks_registered"]
                if self._stats["hooks_registered"] > 0
                else 0
            ),
        }

    def reset_stats(self) -> None:
        """Reset adapter statistics."""
        self._stats = {
            "hooks_registered": 0,
            "hooks_converted": 0,
            "plugins_tracked": len(self._plugin_instances),
        }
        logger.debug("HookAdapter statistics reset")

    def clear(self) -> None:
        """Clear all plugin instances and reset state.

        Warning: This should only be used during testing or shutdown.
        """
        self._plugin_instances.clear()
        self._registered_plugins.clear()
        self.reset_stats()
        logger.info("HookAdapter cleared")
