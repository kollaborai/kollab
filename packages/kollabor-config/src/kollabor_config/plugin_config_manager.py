"""Plugin configuration manager for dynamic schema registration and widget generation."""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Type

from .plugin_schema import PluginConfigSchema

if TYPE_CHECKING:
    from kollabor_plugins.discovery import PluginDiscovery

logger = logging.getLogger(__name__)


def _has_method(obj: Any, method_name: str) -> bool:
    """Check if object has a callable method."""
    return hasattr(obj, method_name) and callable(getattr(obj, method_name, None))


def _get_plugin_config_safely(plugin_class: Any) -> Dict[str, Any]:
    """Safely get default config from a plugin class."""
    try:
        if hasattr(plugin_class, "get_default_config") and callable(
            getattr(plugin_class, "get_default_config", None)
        ):
            return plugin_class.get_default_config() or {}
    except Exception as e:
        logger.debug(f"Failed to get default config from {plugin_class}: {e}")
    return {}


class PluginConfigManager:
    """Manages dynamic plugin configuration schemas and widget generation.

    This manager coordinates between the plugin discovery system and the
    configuration UI system to automatically generate widgets for plugin
    configuration based on plugin-defined schemas.
    """

    def __init__(self, plugin_discovery: "PluginDiscovery"):
        """Initialize the plugin config manager.

        Args:
            plugin_discovery: Plugin discovery instance for accessing loaded plugins.
        """
        self.plugin_discovery = plugin_discovery
        self.plugin_schemas: Dict[str, PluginConfigSchema] = {}
        self.widget_definitions: List[Dict[str, Any]] = []
        logger.info("PluginConfigManager initialized")

    def register_plugin_schema(
        self, plugin_name: str, schema: PluginConfigSchema
    ) -> None:
        """Register a plugin's configuration schema.

        Args:
            plugin_name: Name of the plugin.
            schema: Plugin configuration schema.
        """
        self.plugin_schemas[plugin_name] = schema
        logger.info(f"Registered config schema for plugin: {plugin_name}")

        # Update widget definitions
        self._rebuild_widget_definitions()

    def discover_plugin_schemas(self) -> None:
        """Discover and register schemas from all loaded plugins."""
        logger.info("Discovering plugin configuration schemas...")

        for plugin_name, plugin_class in self.plugin_discovery.loaded_classes.items():
            try:
                # Check if plugin has a get_config_schema method
                if _has_method(plugin_class, "get_config_schema"):
                    logger.debug(f"Plugin {plugin_name} has get_config_schema method")

                    # Get schema from plugin
                    schema = plugin_class.get_config_schema()

                    if isinstance(schema, PluginConfigSchema):
                        self.register_plugin_schema(plugin_name, schema)
                    else:
                        logger.warning(
                            f"Plugin {plugin_name} returned invalid schema type: {type(schema)}"
                        )

                # Fallback: try to create schema from get_default_config
                elif _has_method(plugin_class, "get_default_config"):
                    logger.debug(
                        f"Creating schema from default config for plugin: {plugin_name}"
                    )
                    schema = self._create_schema_from_default_config(
                        plugin_name, plugin_class
                    )
                    if schema:
                        self.register_plugin_schema(plugin_name, schema)
                else:
                    logger.debug(f"Plugin {plugin_name} has no configuration schema")

            except Exception as e:
                logger.error(f"Failed to discover schema for plugin {plugin_name}: {e}")

        logger.info(f"Discovered schemas for {len(self.plugin_schemas)} plugins")

    def _create_schema_from_default_config(
        self, plugin_name: str, plugin_class: Type
    ) -> Optional[PluginConfigSchema]:
        """Create a basic schema from a plugin's default config.

        Args:
            plugin_name: Name of the plugin.
            plugin_class: Plugin class.

        Returns:
            Basic schema if successful, None otherwise.
        """
        try:
            default_config = _get_plugin_config_safely(plugin_class)
            if not default_config:
                return None

            from .plugin_schema import ConfigSchemaBuilder, ValidationType

            builder = ConfigSchemaBuilder(
                plugin_name, f"Auto-generated schema for {plugin_name}"
            )

            # Add 'enabled' field if present
            if "enabled" in default_config:
                builder.add_checkbox(
                    key="enabled",
                    label="Enabled",
                    default=default_config["enabled"],
                    help_text="Enable or disable this plugin",
                )

            # Add other fields with basic type inference
            for key, value in default_config.items():
                if key == "enabled":
                    continue  # Already handled

                if isinstance(value, bool):
                    builder.add_checkbox(
                        key=key,
                        label=key.replace("_", " ").title(),
                        default=value,
                        help_text=f"Configuration option: {key}",
                    )
                elif isinstance(value, (int, float)):
                    if isinstance(value, int):
                        builder.add_slider(
                            key=key,
                            label=key.replace("_", " ").title(),
                            default=value,
                            min_value=0,
                            max_value=max(100, value * 2),
                            help_text=f"Numeric configuration: {key}",
                        )
                    else:
                        builder.add_slider(
                            key=key,
                            label=key.replace("_", " ").title(),
                            default=value,
                            min_value=0.0,
                            max_value=max(1.0, value * 2),
                            step=0.1,
                            help_text=f"Float configuration: {key}",
                        )
                elif isinstance(value, str):
                    builder.add_text_input(
                        key=key,
                        label=key.replace("_", " ").title(),
                        default=value,
                        help_text=f"Text configuration: {key}",
                    )
                elif isinstance(value, list):
                    # For lists, assume it's a dropdown of options
                    if len(value) <= 10:  # Reasonable limit for dropdown
                        builder.add_dropdown(
                            key=key,
                            label=key.replace("_", " ").title(),
                            options=value,
                            default=value[0] if value else "",
                            help_text=f"Select from options: {key}",
                        )
                    else:
                        builder.add_text_input(
                            key=key,
                            label=key.replace("_", " ").title(),
                            default=str(value),
                            help_text=f"List configuration: {key}",
                        )
                elif isinstance(value, dict):
                    # For nested dicts, create a text input with JSON
                    builder.add_text_input(
                        key=key,
                        label=key.replace("_", " ").title(),
                        default=str(value),
                        validation_type=ValidationType.JSON,
                        help_text=f"JSON configuration: {key}",
                    )

            return builder.build()

        except Exception as e:
            logger.error(
                f"Failed to create schema from default config for {plugin_name}: {e}"
            )
            return None

    def get_plugin_schema(self, plugin_name: str) -> Optional[PluginConfigSchema]:
        """Get the configuration schema for a specific plugin.

        Args:
            plugin_name: Name of the plugin.

        Returns:
            Plugin schema if found, None otherwise.
        """
        return self.plugin_schemas.get(plugin_name)

    def get_all_schemas(self) -> Dict[str, PluginConfigSchema]:
        """Get all registered plugin schemas.

        Returns:
            Dictionary mapping plugin names to their schemas.
        """
        return self.plugin_schemas.copy()

    def _rebuild_widget_definitions(self) -> None:
        """Rebuild the complete widget definitions list."""
        self.widget_definitions = []

        for plugin_name, schema in self.plugin_schemas.items():
            plugin_widgets = schema.to_widget_definitions()
            self.widget_definitions.extend(plugin_widgets)
            logger.debug(
                f"Added {len(plugin_widgets)} widgets for plugin: {plugin_name}"
            )

        logger.info(
            f"Rebuilt widget definitions: {len(self.widget_definitions)} total widgets"
        )

    def get_widget_definitions(self) -> List[Dict[str, Any]]:
        """Get all widget definitions for all registered plugins.

        Returns:
            List of widget definition dictionaries.
        """
        if not self.widget_definitions:
            self._rebuild_widget_definitions()

        return self.widget_definitions.copy()

    def get_plugin_config_sections(self) -> List[Dict[str, Any]]:
        """Get UI sections for plugin configuration.

        Returns:
            List of section definitions for the configuration UI.
        """
        sections = []

        # Group plugins by category
        plugins_by_category: Dict[str, list] = {}
        for plugin_name, schema in self.plugin_schemas.items():
            for category in schema.categories:
                if category not in plugins_by_category:
                    plugins_by_category[category] = []
                plugins_by_category[category].append((plugin_name, schema))

        # Create sections for each category
        for category, plugins in plugins_by_category.items():
            category_widgets = []

            for plugin_name, schema in plugins:
                plugin_widgets = schema.to_widget_definitions()
                # Add plugin name to each widget for identification
                for widget in plugin_widgets:
                    widget["plugin_name"] = plugin_name
                category_widgets.extend(plugin_widgets)

            if category_widgets:
                sections.append(
                    {"title": f"{category} Plugins", "widgets": category_widgets}
                )

        return sections

    def validate_plugin_config(
        self, plugin_name: str, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate a plugin's configuration against its schema.

        Args:
            plugin_name: Name of the plugin.
            config: Configuration to validate.

        Returns:
            Validation result dictionary.
        """
        schema = self.get_plugin_schema(plugin_name)
        if not schema:
            return {
                "valid": False,
                "errors": {"schema": f"No schema found for plugin: {plugin_name}"},
            }

        return schema.validate_config(config)

    def get_plugin_default_config(self, plugin_name: str) -> Dict[str, Any]:
        """Get the default configuration for a plugin.

        Args:
            plugin_name: Name of the plugin.

        Returns:
            Default configuration dictionary.
        """
        schema = self.get_plugin_schema(plugin_name)
        if not schema:
            return {}

        return schema.get_default_config()

    def merge_plugin_configs(self) -> Dict[str, Any]:
        """Merge all plugin configurations into a single dictionary.

        Returns:
            Merged configuration dictionary with all plugin configs.
        """
        merged_config = {}

        for plugin_name, schema in self.plugin_schemas.items():
            plugin_config = schema.get_default_config()
            if plugin_config:
                merged_config[plugin_name] = plugin_config

        return merged_config

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about the plugin configuration system.

        Returns:
            Statistics dictionary.
        """
        total_fields = sum(
            len(schema.fields) for schema in self.plugin_schemas.values()
        )
        total_widgets = len(self.widget_definitions)

        widget_type_counts: Dict[str, int] = {}
        for widget_def in self.widget_definitions:
            widget_type = widget_def.get("type", "unknown")
            widget_type_counts[widget_type] = widget_type_counts.get(widget_type, 0) + 1

        return {
            "registered_plugins": len(self.plugin_schemas),
            "total_fields": total_fields,
            "total_widgets": total_widgets,
            "widget_types": widget_type_counts,
            "categories": list(
                set(
                    category
                    for schema in self.plugin_schemas.values()
                    for category in schema.categories
                )
            ),
        }


# Global instance for easy access
_plugin_config_manager: Optional[PluginConfigManager] = None


def get_plugin_config_manager(
    plugin_discovery: "PluginDiscovery",
) -> PluginConfigManager:
    """Get or create the global plugin config manager instance.

    Args:
        plugin_discovery: Plugin discovery instance.

    Returns:
        PluginConfigManager instance.
    """
    global _plugin_config_manager
    if _plugin_config_manager is None:
        _plugin_config_manager = PluginConfigManager(plugin_discovery)
    return _plugin_config_manager
