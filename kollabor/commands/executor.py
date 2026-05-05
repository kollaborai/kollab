"""Command executor for slash command execution."""

import asyncio
import logging
from typing import Any, Dict

from kollabor_events.models import CommandResult, EventType, SlashCommand  # type: ignore[import-untyped]

from .registry import SlashCommandRegistry

logger = logging.getLogger(__name__)


class SlashCommandExecutor:
    """Executes slash commands with proper event integration.

    Handles command execution, error handling, and event bus integration
    for all command modes and types.
    """

    def __init__(self, command_registry: SlashCommandRegistry) -> None:
        """Initialize the command executor.

        Args:
            command_registry: Registry containing command definitions.
        """
        self.command_registry = command_registry
        self.logger = logger

    async def execute_command(self, command: SlashCommand, event_bus) -> CommandResult:
        """Execute a slash command.

        Args:
            command: Parsed slash command to execute.
            event_bus: Event bus for command lifecycle events.

        Returns:
            Command execution result.
        """
        try:
            # Emit command detection event
            await event_bus.emit_with_hooks(
                EventType.SLASH_COMMAND_DETECTED,
                {
                    "command_name": command.name,
                    "args": command.args,
                    "raw_input": command.raw_input,
                },
                "commands",
            )

            # Look up command definition
            command_def = self.command_registry.get_command(command.name)
            if not command_def:
                error_result = CommandResult(
                    success=False,
                    message=f"Unknown command: /{command.name}",
                    display_type="error",
                )
                await self._emit_command_error(
                    event_bus, command, "command_not_found", error_result
                )
                return error_result

            # Check if command is enabled
            if not command_def.enabled:
                error_result = CommandResult(
                    success=False,
                    message=f"Command /{command.name} is currently disabled",
                    display_type="warning",
                )
                await self._emit_command_error(
                    event_bus, command, "command_disabled", error_result
                )
                return error_result

            # Emit command execution start event
            await event_bus.emit_with_hooks(
                EventType.SLASH_COMMAND_EXECUTE,
                {
                    "command": command,
                    "command_def": command_def,
                    "mode": command_def.mode.value,
                },
                "commands",
            )

            # Execute the command handler
            self.logger.info(
                f"Executing command /{command.name} from plugin {command_def.plugin_name}"
            )

            try:
                # Call the command handler
                if asyncio.iscoroutinefunction(command_def.handler):
                    result = await command_def.handler(command)
                else:
                    result = command_def.handler(command)

                # Ensure result is a CommandResult
                if not isinstance(result, CommandResult):
                    result = CommandResult(
                        success=True,
                        message=(
                            str(result)
                            if result is not None
                            else "Command completed successfully"
                        ),
                    )

                # Emit command completion event
                await event_bus.emit_with_hooks(
                    EventType.SLASH_COMMAND_COMPLETE,
                    {"command": command, "command_def": command_def, "result": result},
                    "commands",
                )

                # Handle modal UI configs
                if result.ui_config and result.ui_config.type == "modal":
                    await self._trigger_modal_mode(result.ui_config, event_bus)
                elif result.ui_config and result.ui_config.type == "status_modal":
                    await self._trigger_status_modal_mode(result.ui_config, event_bus)
                elif result.status_ui:
                    # Handle status_ui component (e.g., from /status command)
                    await self._display_status_ui(result.status_ui, event_bus)
                else:
                    # Handle non-modal command output display
                    await self._display_command_result(result, event_bus)

                self.logger.info(f"Command /{command.name} completed successfully")
                return result

            except Exception as handler_error:
                error_result = CommandResult(
                    success=False,
                    message=f"Command /{command.name} failed: {str(handler_error)}",
                    display_type="error",
                    data={"error": str(handler_error)},
                )

                await self._emit_command_error(
                    event_bus, command, "handler_error", error_result
                )
                self.logger.error(
                    f"Command /{command.name} handler failed: {handler_error}"
                )
                return error_result

        except Exception as e:
            error_result = CommandResult(
                success=False,
                message=f"Internal error executing command /{command.name}",
                display_type="error",
                data={"error": str(e)},
            )

            await self._emit_command_error(
                event_bus, command, "internal_error", error_result
            )
            self.logger.error(f"Internal error executing command /{command.name}: {e}")
            return error_result

    async def _emit_command_error(
        self, event_bus, command: SlashCommand, error_type: str, result: CommandResult
    ) -> None:
        """Emit command error event.

        Args:
            event_bus: Event bus for error events.
            command: Command that failed.
            error_type: Type of error that occurred.
            result: Error result details.
        """
        try:
            await event_bus.emit_with_hooks(
                EventType.SLASH_COMMAND_ERROR,
                {"command": command, "error_type": error_type, "result": result},
                "commands",
            )
        except Exception as e:
            self.logger.error(f"Failed to emit command error event: {e}")

    def get_execution_stats(self) -> Dict[str, Any]:
        """Get execution statistics for monitoring.

        Returns:
            Dictionary with execution statistics.
        """
        # In a full implementation, this would track execution metrics
        return {"registry_stats": self.command_registry.get_registry_stats()}

    async def _display_command_result(self, result: CommandResult, event_bus):
        """Display non-modal command result through event bus.

        Args:
            result: Command result to display.
            event_bus: Event bus for display events.
        """
        try:
            if result.message:
                # Emit command output event for display
                await event_bus.emit_with_hooks(
                    EventType.COMMAND_OUTPUT_DISPLAY,
                    {
                        "message": result.message,
                        "display_type": result.display_type or "info",
                        "success": result.success,
                        "data": result.data,
                    },
                    "command_output",
                )
                self.logger.info(f"Command output displayed: {result.display_type}")
        except Exception as e:
            self.logger.error(f"Error displaying command result: {e}")

    async def _trigger_modal_mode(self, ui_config, event_bus):
        """Trigger modal mode through event bus.

        Args:
            ui_config: UI configuration for the modal.
            event_bus: Event bus for modal events.
        """
        try:
            # Emit modal trigger event that input handler will listen for
            await event_bus.emit_with_hooks(
                EventType.MODAL_TRIGGER,  # Use dedicated modal trigger event
                {"ui_config": ui_config, "action": "show_modal"},
                "modal_trigger",
            )
            # Emit MODAL_SHOW for config hooks
            await event_bus.emit_with_hooks(
                EventType.MODAL_SHOW,
                {"type": "command_modal", "action": "show_modal", "source": "executor"},
                "modal_trigger",
            )
            self.logger.info("Modal UI config triggered through event bus")
        except Exception as e:
            self.logger.error(f"Error triggering modal mode: {e}")

    async def _trigger_status_modal_mode(self, ui_config, event_bus):
        """Trigger status modal mode through event bus.

        Args:
            ui_config: UI configuration for the status modal.
            event_bus: Event bus for modal events.
        """
        try:
            # Emit status modal trigger event that input handler will listen for
            await event_bus.emit_with_hooks(
                EventType.STATUS_MODAL_TRIGGER,  # New event type for status modals
                {"ui_config": ui_config, "action": "show_status_modal"},
                "status_modal_trigger",
            )
            # Emit MODAL_SHOW for config hooks
            await event_bus.emit_with_hooks(
                EventType.MODAL_SHOW,
                {
                    "type": "status_modal",
                    "action": "show_status_modal",
                    "source": "executor",
                },
                "status_modal_trigger",
            )
            self.logger.info("Status modal UI config triggered through event bus")
        except Exception as e:
            self.logger.error(f"Error triggering status modal mode: {e}")

    async def _display_status_ui(self, status_ui, event_bus):
        """Display status UI component through event bus.

        Args:
            status_ui: Status UI component with render() method.
            event_bus: Event bus for display events.
        """
        try:
            # Render the status UI if it has a render method
            if hasattr(status_ui, "render"):
                lines = status_ui.render()
                message = "\n".join(lines) if isinstance(lines, list) else str(lines)
            else:
                message = str(status_ui)

            # Emit status display event
            await event_bus.emit_with_hooks(
                EventType.COMMAND_OUTPUT_DISPLAY,
                {
                    "message": message,
                    "display_type": "info",
                    "success": True,
                    "is_status_ui": True,
                },
                "status_display",
            )
            self.logger.info("Status UI displayed")
        except Exception as e:
            self.logger.error(f"Error displaying status UI: {e}")
