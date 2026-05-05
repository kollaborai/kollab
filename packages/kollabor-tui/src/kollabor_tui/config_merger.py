"""Config persistence system for modal UI changes."""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class ConfigMerger:
    """Handles saving widget changes to config.json using existing config system."""

    @staticmethod
    def apply_widget_changes(
        config_service, widget_changes: Dict[str, Any], save_target: str = "local"
    ) -> bool:
        """Apply widget changes to config.json using existing config system.

        Args:
            config_service: ConfigService instance for managing configuration.
            widget_changes: Dictionary mapping config paths to new values.
            save_target: Where to save config ("local" or "global").

        Returns:
            True if all changes applied successfully, False otherwise.
        """
        try:
            logger.info(f"Applying {len(widget_changes)} widget changes to config")

            # Create backup before making changes
            backup_path = config_service.backup_config(".pre-modal-changes")
            if backup_path:
                logger.debug(f"Config backup created: {backup_path}")

            # Apply all changes atomically
            success_count = 0
            failed_changes = []

            # CRITICAL FIX: Set all changes in memory first, then save once
            # This prevents multiple file writes and redundant config reloads
            for path, value in widget_changes.items():
                try:
                    # Set values in memory only (no disk write yet)
                    if config_service.set(path, value):
                        success_count += 1
                        logger.debug(f"Set in memory: {path} = {value}")
                    else:
                        failed_changes.append((path, value, "set_failed"))
                        logger.error(f"Failed to set config: {path} = {value}")
                except Exception as e:
                    failed_changes.append((path, value, str(e)))
                    logger.error(f"Error setting {path}: {e}")

            # If all changes applied successfully, save once to disk
            if not failed_changes:
                save_success = config_service.save_config(save_target=save_target)
                if not save_success:
                    logger.error("Failed to save config to disk after applying changes")
                    return False

            # Report results
            if failed_changes:
                logger.error(
                    f"Failed to apply {len(failed_changes)} changes: {failed_changes}"
                )
                return False

            logger.info(f"Successfully applied all {success_count} config changes")

            # Notify plugins using existing event system
            ConfigMerger.notify_plugins_config_changed(
                config_service, list(widget_changes.keys())
            )

            return True

        except Exception as e:
            logger.error(f"Critical error applying widget changes: {e}")
            return False

    @staticmethod
    def notify_plugins_config_changed(config_service, changed_paths: List[str]) -> None:
        """Notify plugins their config changed using existing event system.

        Args:
            config_service: ConfigService instance.
            changed_paths: List of config paths that changed.
        """
        try:
            # Trigger the existing config reload callbacks
            config_service._notify_reload_callbacks()
            logger.debug(f"Notified plugins of config changes: {changed_paths}")
        except Exception as e:
            logger.error(f"Error notifying plugins of config changes: {e}")

    @staticmethod
    def collect_widget_changes(widgets: List[Any]) -> Dict[str, Any]:
        """Collect all widget value changes.

        Args:
            widgets: List of widgets to collect changes from.

        Returns:
            Dictionary mapping config paths to new values.
        """
        changes = {}

        for widget in widgets:
            # Check if widget has a pending value that differs from current
            if hasattr(widget, "_pending_value") and hasattr(widget, "config_path"):
                # Use get_value() method instead of current_value attribute
                current_value = (
                    widget.get_value() if hasattr(widget, "get_value") else None
                )
                pending_value = widget._pending_value

                # Only include changes where value actually changed
                # Note: pending_value being None means no change was made
                if pending_value is not None and pending_value != current_value:
                    changes[widget.config_path] = pending_value
                    logger.debug(
                        f"Collected change: {widget.config_path} = {pending_value} (was {current_value})"
                    )

        logger.info(f"Collected {len(changes)} widget changes")
        return changes

    @staticmethod
    def validate_config_changes(
        config_service, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate config changes before applying them.

        Args:
            config_service: ConfigService instance.
            changes: Dictionary of proposed changes.

        Returns:
            Validation result with 'valid' boolean and 'errors' list.
        """
        validation_result: Dict[str, Any] = {
            "valid": True,
            "errors": [],
            "warnings": [],
        }

        try:
            # Create a temporary config to test validation
            original_config = config_service.config_manager.config.copy()

            # Apply changes temporarily
            for path, value in changes.items():
                try:
                    from kollabor_events.dict_utils import safe_set

                    safe_set(config_service.config_manager.config, path, value)
                except Exception as e:
                    validation_result["errors"].append(f"Invalid path {path}: {e}")
                    validation_result["valid"] = False

            # Use existing validation
            if validation_result["valid"]:
                system_validation = config_service.validate_config()
                if not system_validation["valid"]:
                    validation_result["valid"] = False
                    validation_result["errors"].extend(system_validation["errors"])
                validation_result["warnings"].extend(
                    system_validation.get("warnings", [])
                )

            # Restore original config
            config_service.config_manager.config = original_config

        except Exception as e:
            validation_result["valid"] = False
            validation_result["errors"].append(f"Validation error: {e}")

        return validation_result

    @staticmethod
    def get_config_values(config_service, config_paths: List[str]) -> Dict[str, Any]:
        """Get current values for config paths.

        Args:
            config_service: ConfigService instance.
            config_paths: List of config paths to retrieve.

        Returns:
            Dictionary mapping paths to their current values.
        """
        values = {}

        for path in config_paths:
            try:
                value = config_service.get(path)
                values[path] = value
                logger.debug(f"Retrieved config value: {path} = {value}")
            except Exception as e:
                logger.error(f"Error retrieving config {path}: {e}")
                values[path] = None

        return values
