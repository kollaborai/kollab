"""Event system for plugin communication."""

import logging
from typing import Any, Dict, Optional, Union

from .models import EventType, Hook
from .processor import EventProcessor
from .registry import HookRegistry

logger = logging.getLogger(__name__)

from .executor import HookExecutor  # noqa: E402


class EventBus:
    """Simplified event bus system for plugin communication.

    Coordinates between specialized components for hook registration
    and event processing with clean separation of concerns.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the event bus with specialized components.

        Args:
            config: Configuration dictionary for hook defaults.
        """
        self.config = config or {}
        self._services: Dict[str, Any] = {}
        self.hook_registry = HookRegistry()
        self.hook_executor = HookExecutor(config=self.config)
        self.event_processor = EventProcessor(self.hook_registry, self.hook_executor)
        logger.info("Event bus initialized with specialized components")

    def register_service(self, name: str, service: Any) -> None:
        """Register a service for lookup by other components.

        Args:
            name: Service identifier (e.g., "llm_service", "permission_manager").
            service: The service instance.
        """
        self._services[name] = service
        logger.debug(f"Service registered: {name}")

    def get_service(self, name: str) -> Optional[Any]:
        """Get a registered service by name.

        Args:
            name: Service identifier.

        Returns:
            The service instance, or None if not registered.
        """
        return self._services.get(name)

    def has_service(self, name: str) -> bool:
        """Check if a service is registered.

        Args:
            name: Service identifier.

        Returns:
            True if service is registered, False otherwise.
        """
        return name in self._services

    async def register_hook(self, hook: Hook) -> bool:
        """Register a hook with the event bus.

        Note: Config defaults are applied during execution in HookExecutor,
        not during registration. This allows runtime config changes to take
        effect immediately without re-registering hooks.

        Args:
            hook: The hook to register.

        Returns:
            True if registration successful, False otherwise.
        """
        success = self.hook_registry.register_hook(hook)
        if success:
            logger.debug(
                f"Successfully registered hook: {hook.plugin_name}.{hook.name}"
            )
        else:
            logger.error(f"Failed to register hook: {hook.plugin_name}.{hook.name}")
        return success

    async def unregister_hook(self, plugin_name: str, hook_name: str) -> bool:
        """Unregister a hook from the event bus.

        Args:
            plugin_name: Name of the plugin that owns the hook.
            hook_name: Name of the hook.

        Returns:
            True if unregistration successful, False otherwise.
        """
        return self.hook_registry.unregister_hook(plugin_name, hook_name)

    async def emit_with_hooks(
        self, event_type: Union[EventType, str], data: Dict[str, Any], source: str
    ) -> Dict[str, Any]:
        """Emit an event with pre/post hook processing.

        For string event types (plugin custom events), only MAIN phase is processed.
        For EventType enum events, pre/post phases are also processed if mapped.

        Args:
            event_type: Type of event to emit (EventType enum or str for custom events).
            data: Event data.
            source: Source of the event.

        Returns:
            Results from hook processing.
        """
        return await self.event_processor.process_event_with_phases(
            event_type, data, source
        )

    def get_hook_status(self) -> Dict[str, Any]:
        """Get current status of all registered hooks.

        Returns:
            Dictionary with hook status information.
        """
        return self.hook_registry.get_hook_status_summary()

    def get_registry_stats(self) -> Dict[str, Any]:
        """Get comprehensive registry statistics.

        Returns:
            Dictionary with detailed registry statistics.
        """
        return self.hook_registry.get_registry_stats()

    def enable_hook(self, plugin_name: str, hook_name: str) -> bool:
        """Enable a registered hook.

        Args:
            plugin_name: Name of the plugin that owns the hook.
            hook_name: Name of the hook.

        Returns:
            True if hook was enabled, False otherwise.
        """
        return self.hook_registry.enable_hook(plugin_name, hook_name)

    def disable_hook(self, plugin_name: str, hook_name: str) -> bool:
        """Disable a registered hook.

        Args:
            plugin_name: Name of the plugin that owns the hook.
            hook_name: Name of the hook.

        Returns:
            True if hook was disabled, False otherwise.
        """
        return self.hook_registry.disable_hook(plugin_name, hook_name)

    def get_hooks_for_event(self, event_type: Union[EventType, str]) -> int:
        """Get the number of hooks registered for an event type.

        Args:
            event_type: The event type to check (EventType enum or str).

        Returns:
            Number of hooks registered for the event type.
        """
        hooks = self.hook_registry.get_hooks_for_event(event_type)
        return len(hooks)

    def add_event_type_mapping(
        self, main_event: EventType, pre_event: EventType, post_event: EventType
    ) -> None:
        """Add a new event type mapping for pre/post processing.

        Args:
            main_event: The main event type.
            pre_event: The pre-processing event type.
            post_event: The post-processing event type.
        """
        self.event_processor.add_event_type_mapping(main_event, pre_event, post_event)
