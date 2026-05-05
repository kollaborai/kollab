"""Modal action handlers for save/cancel functionality."""

import logging
from typing import Any, Dict, List

# Import from same package (kollabor-tui)
from kollabor_tui.config_merger import ConfigMerger

logger = logging.getLogger(__name__)


class ModalActionHandler:
    """Handles modal actions like save/cancel with config persistence."""

    def __init__(self, config_service):
        """Initialize action handler with config service.

        Args:
            config_service: ConfigService instance for persistence.
        """
        self.config_service = config_service

    async def handle_action(
        self, action: str, widgets: List[Any], save_target: str = "local"
    ) -> Dict[str, Any]:
        """Handle modal action (save/cancel).

        Args:
            action: Action to perform ("save" or "cancel").
            widgets: List of widgets to collect values from.
            save_target: Where to save config ("local" or "global").

        Returns:
            Action result with success status and message.
        """
        try:
            if action == "save":
                return await self._handle_save_action(widgets, save_target=save_target)
            elif action == "cancel":
                return await self._handle_cancel_action()
            else:
                return {
                    "success": False,
                    "message": f"Unknown action: {action}",
                    "action": action,
                }

        except Exception as e:
            logger.error(f"Error handling modal action {action}: {e}")
            return {
                "success": False,
                "message": f"Error handling action: {str(e)}",
                "action": action,
            }

    async def _handle_save_action(
        self, widgets: List[Any], save_target: str = "local"
    ) -> Dict[str, Any]:
        """Handle save action with config persistence.

        Args:
            widgets: List of widgets to save values from.
            save_target: Where to save config ("local" or "global").

        Returns:
            Save action result.
        """
        try:
            logger.info(f"=== SAVE ACTION: Processing {len(widgets)} widgets ===")
            for i, w in enumerate(widgets):
                path = getattr(w, "config_path", "N/A")
                pending = getattr(w, "_pending_value", "N/A")
                logger.info(
                    f"  Widget {i}: {w.__class__.__name__} path={path} pending={pending}"
                )

            # Collect widget changes
            changes = ConfigMerger.collect_widget_changes(widgets)
            logger.info(
                f"=== SAVE ACTION: Collected {len(changes)} changes: {changes} ==="
            )

            if not changes:
                return {
                    "success": True,
                    "message": "No changes to save",
                    "action": "save",
                    "changes_count": 0,
                }

            # Validate changes before applying
            validation = ConfigMerger.validate_config_changes(
                self.config_service, changes
            )
            if not validation["valid"]:
                error_msg = f"Invalid configuration: {', '.join(validation['errors'])}"
                logger.error(error_msg)
                return {
                    "success": False,
                    "message": error_msg,
                    "action": "save",
                    "validation_errors": validation["errors"],
                }

            # Apply changes
            success = ConfigMerger.apply_widget_changes(
                self.config_service, changes, save_target=save_target
            )

            if success:
                logger.info(f"Successfully saved {len(changes)} configuration changes")

                # Sync widget cached values so they reflect saved state
                for widget in widgets:
                    config_path = getattr(widget, "config_path", None)
                    if (
                        config_path
                        and hasattr(widget, "_pending_value")
                        and widget._pending_value is not None
                    ):
                        if "current_value" in widget.config:
                            widget.config["current_value"] = widget._pending_value
                        if "value" in widget.config:
                            widget.config["value"] = widget._pending_value
                        widget._pending_value = None

                # Trigger hot reload for all registered services
                self.config_service._notify_reload_callbacks()
                logger.info("Hot reload callbacks triggered")

                return {
                    "success": True,
                    "message": f"Saved {len(changes)} configuration changes",
                    "action": "save",
                    "changes_count": len(changes),
                    "changes": changes,
                }
            else:
                return {
                    "success": False,
                    "message": "Failed to save configuration changes",
                    "action": "save",
                    "changes_count": len(changes),
                }

        except Exception as e:
            logger.error(f"Error in save action: {e}")
            return {
                "success": False,
                "message": f"Save failed: {str(e)}",
                "action": "save",
            }

    async def _handle_cancel_action(self) -> Dict[str, Any]:
        """Handle cancel action (no persistence).

        Returns:
            Cancel action result.
        """
        logger.info("Modal cancelled - no changes saved")
        return {
            "success": True,
            "message": "Configuration changes cancelled",
            "action": "cancel",
        }

    def get_save_confirmation_message(self, changes: Dict[str, Any]) -> str:
        """Generate save confirmation message.

        Args:
            changes: Dictionary of config changes.

        Returns:
            Formatted confirmation message.
        """
        if not changes:
            return "No changes to save"

        change_list = []
        for path, value in changes.items():
            # Truncate long values for display
            display_value = str(value)
            if len(display_value) > 30:
                display_value = display_value[:27] + "..."
            change_list.append(f"{path} = {display_value}")

        changes_text = "\n".join(f"  • {change}" for change in change_list[:5])
        if len(changes) > 5:
            changes_text += f"\n  ... and {len(changes) - 5} more"

        return f"Save {len(changes)} configuration changes?\n\n{changes_text}"

    def get_cancel_confirmation_message(self) -> str:
        """Generate cancel confirmation message.

        Returns:
            Formatted confirmation message.
        """
        return "Cancel configuration changes?\n\nAll unsaved changes will be lost."
