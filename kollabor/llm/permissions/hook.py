"""Permission hook for event bus integration."""

import logging
from typing import Any, Dict

from kollabor_agent.permissions import PermissionManager

logger = logging.getLogger(__name__)


class PermissionHook:
    """
    Event bus hook for enforcing tool permissions.

    Registers at SECURITY priority (900) to intercept tool execution
    before it happens.
    """

    def __init__(self, permission_manager: PermissionManager):
        """Initialize permission hook.

        Args:
            permission_manager: Permission manager instance
        """
        self._permission_manager = permission_manager

    async def register(self, event_bus: Any) -> None:
        """Register hooks with event bus.

        Args:
            event_bus: Event bus instance
        """
        # Import Hook and EventType here to avoid circular imports
        from kollabor_events.models import EventType, Hook, HookPriority

        # Create Hook object for registration
        hook = Hook(
            plugin_name="permission_system",
            name="permission_check",
            event_type=EventType.TOOL_CALL_PRE,
            callback=self._handle_tool_pre,
            priority=HookPriority.SECURITY.value,
            enabled=True,
        )

        await event_bus.register_hook(hook)

        logger.debug("Permission hook registered at SECURITY priority")

    async def _handle_tool_pre(
        self,
        data: Dict[str, Any],
        event: Any,  # Event
    ) -> Dict[str, Any]:
        """
        Handle TOOL_CALL_PRE event.

        Checks permission before tool execution. If denied,
        sets event.cancelled = True to prevent execution.

        Args:
            data: Event data containing tool_data
            event: Event object

        Returns:
            Modified data dict
        """
        tool_data = data.get("tool_data", {})

        if not tool_data:
            logger.warning("TOOL_CALL_PRE received without tool_data")
            return data

        # Check permission
        decision = await self._permission_manager.check_permission(tool_data)

        # Add decision to data for downstream hooks
        data["permission_decision"] = {
            "allowed": decision.allowed,
            "reason": decision.reason,
            "risk_level": decision.risk_level.name,
        }

        if not decision.allowed:
            # Cancel the event to prevent tool execution
            event.cancelled = True
            event.cancel_reason = decision.reason

            logger.info(
                f"Tool execution denied: {tool_data.get('name', 'unknown')} - "
                f"{decision.reason}"
            )
        else:
            logger.debug(
                f"Tool execution approved: {tool_data.get('name', 'unknown')} - "
                f"{decision.reason}"
            )

        return data
