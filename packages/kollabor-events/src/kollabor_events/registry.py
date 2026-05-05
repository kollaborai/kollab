"""Hook registry for managing hook registration and lifecycle."""

import logging
from collections import defaultdict
from typing import Any, Dict, List, Union

from .models import EventType, Hook, HookStatus

logger = logging.getLogger(__name__)


class HookRegistry:
    """Manages hook registration, organization, and status tracking.

    This class is responsible for maintaining the registry of hooks,
    organizing them by event type and priority, and tracking their status.
    """

    def __init__(self):
        """Initialize the hook registry."""
        from typing import Union

        # Support both EventType enum and string event types
        self.hooks: Dict[Union[EventType, str], List[Hook]] = defaultdict(list)
        self.hook_status: Dict[str, HookStatus] = {}
        self.hook_metadata: Dict[str, Dict[str, Any]] = {}
        logger.info("HookRegistry initialized")

    def register_hook(self, hook: Hook) -> bool:
        """Register a hook with the registry.

        Args:
            hook: The hook to register.

        Returns:
            True if registration successful, False otherwise.
        """
        try:
            hook_key = f"{hook.plugin_name}.{hook.name}"

            # Check for duplicate registration
            if hook_key in self.hook_status:
                logger.warning(
                    f"Hook {hook_key} already registered, updating registration"
                )
                self._remove_hook_from_lists(hook_key)

            # Add hook to appropriate event type list
            self.hooks[hook.event_type].append(hook)

            # Sort hooks by priority (highest first)
            # Handle both int and HookPriority enum values
            def get_priority_value(h):
                if isinstance(h.priority, int):
                    return h.priority
                else:
                    # Assume it's a HookPriority enum, get its value
                    return (
                        h.priority.value
                        if hasattr(h.priority, "value")
                        else int(h.priority)
                    )

            self.hooks[hook.event_type].sort(key=get_priority_value, reverse=True)

            # Track hook status and metadata
            self.hook_status[hook_key] = HookStatus.PENDING
            event_type_str = (
                hook.event_type.value
                if hasattr(hook.event_type, "value")
                else hook.event_type
            )
            self.hook_metadata[hook_key] = {
                "event_type": event_type_str,
                "priority": hook.priority,
                "plugin_name": hook.plugin_name,
                "enabled": hook.enabled,
                "timeout": hook.timeout,
                "error_action": hook.error_action,
                "registration_order": len(self.hook_status),
            }

            logger.debug(
                f"Registered hook: {hook_key} for {event_type_str} (priority: {hook.priority})"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to register hook {hook.plugin_name}.{hook.name}: {e}")
            return False

    def unregister_hook(self, plugin_name: str, hook_name: str) -> bool:
        """Unregister a hook from the registry.

        Args:
            plugin_name: Name of the plugin that owns the hook.
            hook_name: Name of the hook.

        Returns:
            True if unregistration successful, False otherwise.
        """
        hook_key = f"{plugin_name}.{hook_name}"

        if hook_key not in self.hook_status:
            logger.warning(f"Hook {hook_key} not found for unregistration")
            return False

        try:
            # Remove from hook lists
            removed = self._remove_hook_from_lists(hook_key)

            if removed:
                # Clean up status and metadata
                del self.hook_status[hook_key]
                del self.hook_metadata[hook_key]
                logger.info(f"Unregistered hook: {hook_key}")
                return True
            else:
                logger.warning(f"Hook {hook_key} not found in any event type lists")
                return False

        except Exception as e:
            logger.error(f"Failed to unregister hook {hook_key}: {e}")
            return False

    def get_hooks_for_event(self, event_type: Union[EventType, str]) -> List[Hook]:
        """Get all hooks registered for a specific event type.

        Args:
            event_type: The event type to get hooks for (EventType enum or str).

        Returns:
            List of hooks sorted by priority (highest first).
        """

        hooks = self.hooks.get(event_type, [])

        # Filter out disabled hooks
        enabled_hooks = [hook for hook in hooks if hook.enabled]

        # logger.debug(f"Retrieved {len(enabled_hooks)} enabled hooks for {event_type.value}")
        return enabled_hooks

    def update_hook_status(self, hook_key: str, status: str) -> bool:
        """Update the status of a registered hook.

        Args:
            hook_key: Key identifying the hook (plugin_name.hook_name).
            status: New status for the hook.

        Returns:
            True if update successful, False otherwise.
        """
        try:
            if hook_key not in self.hook_status:
                logger.warning(f"Cannot update status for unknown hook: {hook_key}")
                return False

            # Convert string status to HookStatus enum
            status_mapping = {
                "pending": HookStatus.PENDING,
                "working": HookStatus.WORKING,
                "completed": HookStatus.COMPLETED,
                "failed": HookStatus.FAILED,
                "timeout": HookStatus.TIMEOUT,
            }

            if status not in status_mapping:
                logger.error(f"Invalid status '{status}' for hook {hook_key}")
                return False

            old_status = self.hook_status[hook_key]  # noqa: F841 - useful for debugging
            self.hook_status[hook_key] = status_mapping[status]

            return True

        except Exception as e:
            logger.error(f"Failed to update status for hook {hook_key}: {e}")
            return False

    def get_hook_status_summary(self) -> Dict[str, Any]:
        """Get a summary of all hook statuses.

        Returns:
            Dictionary with hook status statistics and details.
        """
        status_counts: Dict[str, int] = defaultdict(int)
        hook_details = {}

        for hook_key, status in self.hook_status.items():
            status_counts[status.value] += 1

            metadata = self.hook_metadata.get(hook_key, {})
            hook_details[hook_key] = {
                "status": status.value,
                "event_type": metadata.get("event_type", "unknown"),
                "priority": metadata.get("priority", 0),
                "enabled": metadata.get("enabled", True),
                "plugin_name": metadata.get("plugin_name", "unknown"),
            }

        return {
            "total_hooks": len(self.hook_status),
            "status_counts": dict(status_counts),
            "hook_details": hook_details,
        }

    def enable_hook(self, plugin_name: str, hook_name: str) -> bool:
        """Enable a registered hook.

        Args:
            plugin_name: Name of the plugin that owns the hook.
            hook_name: Name of the hook.

        Returns:
            True if hook was enabled, False otherwise.
        """
        return self._set_hook_enabled(plugin_name, hook_name, True)

    def disable_hook(self, plugin_name: str, hook_name: str) -> bool:
        """Disable a registered hook.

        Args:
            plugin_name: Name of the plugin that owns the hook.
            hook_name: Name of the hook.

        Returns:
            True if hook was disabled, False otherwise.
        """
        return self._set_hook_enabled(plugin_name, hook_name, False)

    def _set_hook_enabled(
        self, plugin_name: str, hook_name: str, enabled: bool
    ) -> bool:
        """Set the enabled state of a hook.

        Args:
            plugin_name: Name of the plugin that owns the hook.
            hook_name: Name of the hook.
            enabled: Whether to enable or disable the hook.

        Returns:
            True if state was changed, False otherwise.
        """
        hook_key = f"{plugin_name}.{hook_name}"

        # Find the hook in the registry
        hook_found = False
        for event_type, hooks in self.hooks.items():
            for hook in hooks:
                if f"{hook.plugin_name}.{hook.name}" == hook_key:
                    hook.enabled = enabled
                    hook_found = True

                    # Update metadata
                    if hook_key in self.hook_metadata:
                        self.hook_metadata[hook_key]["enabled"] = enabled

                    action = "enabled" if enabled else "disabled"
                    logger.info(f"Hook {hook_key} {action}")
                    break
            if hook_found:
                break

        if not hook_found:
            logger.warning(f"Hook {hook_key} not found for enable/disable")
            return False

        return True

    def _remove_hook_from_lists(self, hook_key: str) -> bool:
        """Remove a hook from all event type lists.

        Args:
            hook_key: Key identifying the hook to remove.

        Returns:
            True if hook was found and removed, False otherwise.
        """
        removed = False

        for event_type, hooks in self.hooks.items():
            # Find and remove hook from this event type's list
            for i, hook in enumerate(hooks):
                if f"{hook.plugin_name}.{hook.name}" == hook_key:
                    del hooks[i]
                    removed = True
                    break

        return removed

    def get_registry_stats(self) -> Dict[str, Any]:
        """Get comprehensive registry statistics.

        Returns:
            Dictionary with detailed registry statistics.
        """
        event_type_counts = {}
        priority_distribution: Dict[int, int] = defaultdict(int)
        plugin_counts: Dict[str, int] = defaultdict(int)

        for event_type, hooks in self.hooks.items():
            event_type_counts[
                event_type.value if hasattr(event_type, "value") else str(event_type)
            ] = len(hooks)

            for hook in hooks:
                priority_distribution[hook.priority] += 1
                plugin_counts[hook.plugin_name] += 1

        return {
            "total_hooks": len(self.hook_status),
            "event_types": len(self.hooks),
            "hooks_per_event_type": event_type_counts,
            "priority_distribution": dict(priority_distribution),
            "hooks_per_plugin": dict(plugin_counts),
            "status_summary": self.get_hook_status_summary(),
        }
