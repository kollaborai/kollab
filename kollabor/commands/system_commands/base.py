"""Base class for system command handlers."""

import logging
from typing import Dict, Set


class BaseCommandHandler:
    """Base class for domain command handlers.

    Provides common functionality and service access patterns for all
    system command handlers. Handlers inherit from this class and
    implement their specific commands and modal actions.

    Subclasses define which actions they handle via MODAL_ACTIONS set.
    """

    # Subclasses define which actions they handle
    MODAL_ACTIONS: Set[str] = set()

    def __init__(
        self,
        command_registry,
        event_bus,
        config_manager=None,
    ):
        """Initialize base command handler.

        Args:
            command_registry: For registering slash commands.
            event_bus: For hooks AND service lookup.
            config_manager: For configuration access (optional, use service registry).
        """
        self.command_registry = command_registry
        self.event_bus = event_bus
        self._config_manager = config_manager
        self.logger = logging.getLogger(self.__class__.__name__)

    @property
    def config_manager(self):
        """Get config manager via service registry or direct reference."""
        if self._config_manager is not None:
            return self._config_manager
        return self.event_bus.get_service("config_manager")

    # Note: Service accessors (llm_service, profile_manager, agent_manager,
    # permission_manager) are defined by subclasses as needed. They can either
    # set instance attributes directly or define their own properties for
    # dynamic lookup via event_bus.get_service().

    def register_commands(self) -> None:
        """Register domain commands.

        Override this method in subclasses to register commands.
        """
        raise NotImplementedError("Subclasses must implement register_commands()")

    async def handle_modal_action(self, action: str, data: Dict) -> Dict:
        """Handle domain-specific modal actions.

        Contract:
        - MUTATE data dict in-place
        - Set data["display_messages"] for user feedback
        - Set data["show_modal"] to open another modal
        - Set data["close_modal"] = True to close current modal
        - Return the same data dict (for chaining)

        Args:
            action: The modal action being handled.
            data: Event data dictionary (mutated in-place).

        Returns:
            The mutated data dictionary.
        """
        self.logger.warning(f"Unhandled modal action: {action}")
        return data
