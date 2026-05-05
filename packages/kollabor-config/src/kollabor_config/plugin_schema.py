"""Plugin configuration schema system for dynamic widget generation.

This module provides a comprehensive system for plugins to define their
configuration schemas, which automatically generates UI widgets and validation.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class WidgetType(Enum):
    """Supported widget types for plugin configuration."""

    CHECKBOX = "checkbox"
    SLIDER = "slider"
    TEXT_INPUT = "text_input"
    DROPDOWN = "dropdown"
    COLOR_PICKER = "color_picker"
    FILE_PICKER = "file_picker"
    DIRECTORY_PICKER = "directory_picker"
    MULTI_SELECT = "multi_select"
    KEY_VALUE = "key_value"
    CODE_EDITOR = "code_editor"


class ValidationType(Enum):
    """Supported validation types for config fields."""

    NONE = "none"
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    URL = "url"
    EMAIL = "email"
    FILE_PATH = "file_path"
    DIRECTORY_PATH = "directory_path"
    JSON = "json"
    REGEX = "regex"


@dataclass
class ValidationRule:
    """Validation rule for a configuration field."""

    type: ValidationType = ValidationType.NONE
    min_value: Optional[Union[int, float]] = None
    max_value: Optional[Union[int, float]] = None
    pattern: Optional[str] = None
    custom_validator: Optional[Callable[[Any], bool]] = None
    error_message: Optional[str] = None

    def validate(self, value: Any) -> tuple[bool, Optional[str]]:
        """Validate a value against this rule.

        Args:
            value: Value to validate.

        Returns:
            Tuple of (is_valid, error_message).
        """
        try:
            # Type validation
            if self.type == ValidationType.STRING:
                if not isinstance(value, str):
                    return False, self.error_message or "Value must be a string"
            elif self.type == ValidationType.INTEGER:
                if not isinstance(value, int):
                    return False, self.error_message or "Value must be an integer"
            elif self.type == ValidationType.FLOAT:
                if not isinstance(value, (int, float)):
                    return False, self.error_message or "Value must be a number"
            elif self.type == ValidationType.BOOLEAN:
                if not isinstance(value, bool):
                    return False, self.error_message or "Value must be a boolean"

            # Range validation
            if self.min_value is not None and value < self.min_value:
                return False, self.error_message or f"Value must be >= {self.min_value}"
            if self.max_value is not None and value > self.max_value:
                return False, self.error_message or f"Value must be <= {self.max_value}"

            # Pattern validation
            if self.pattern and isinstance(value, str):
                import re

                if not re.match(self.pattern, value):
                    return (
                        False,
                        self.error_message
                        or f"Value must match pattern: {self.pattern}",
                    )

            # Custom validation
            if self.custom_validator:
                if not self.custom_validator(value):
                    return False, self.error_message or "Custom validation failed"

            return True, None

        except Exception as e:
            logger.error(f"Validation error: {e}")
            return False, self.error_message or f"Validation error: {e}"


@dataclass
class ConfigField:
    """Configuration field definition for plugins."""

    key: str
    widget_type: WidgetType
    label: str
    default_value: Any
    help_text: str = ""
    validation: ValidationRule = field(default_factory=ValidationRule)

    # Widget-specific options
    options: List[str] = field(default_factory=list)  # For dropdown, multi_select
    min_value: Optional[Union[int, float]] = None  # For slider
    max_value: Optional[Union[int, float]] = None  # For slider
    step: Optional[Union[int, float]] = None  # For slider
    placeholder: str = ""  # For text_input
    file_extensions: List[str] = field(default_factory=list)  # For file_picker

    # Display options
    category: str = "General"
    advanced: bool = False
    requires_restart: bool = False
    environment_dependent: bool = False


@dataclass
class PluginConfigSchema:
    """Complete configuration schema for a plugin."""

    plugin_name: str
    plugin_version: str = "1.0.0"
    description: str = ""
    categories: List[str] = field(default_factory=lambda: ["General"])
    fields: List[ConfigField] = field(default_factory=list)

    def add_field(self, field: ConfigField) -> "PluginConfigSchema":
        """Add a field to the schema.

        Args:
            field: Field to add.

        Returns:
            Self for method chaining.
        """
        self.fields.append(field)
        if field.category not in self.categories:
            self.categories.append(field.category)
        return self

    def get_fields_by_category(self, category: str) -> List[ConfigField]:
        """Get all fields in a specific category.

        Args:
            category: Category to filter by.

        Returns:
            List of fields in the category.
        """
        return [f for f in self.fields if f.category == category]

    def get_field(self, key: str) -> Optional[ConfigField]:
        """Get a field by its key.

        Args:
            key: Field key to find.

        Returns:
            Field if found, None otherwise.
        """
        for schema_field in self.fields:
            if schema_field.key == key:
                return schema_field
        return None

    def validate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a configuration dictionary against this schema.

        Args:
            config: Configuration to validate.

        Returns:
            Dictionary with validation results.
        """
        result: Dict[str, Any] = {"valid": True, "errors": {}, "warnings": {}}

        for schema_field in self.fields:
            value = config.get(schema_field.key, schema_field.default_value)

            # Validate the value
            is_valid, error_msg = schema_field.validation.validate(value)

            if not is_valid:
                result["valid"] = False
                result["errors"][schema_field.key] = error_msg

            # Check for deprecated or advanced fields
            if schema_field.advanced and value != schema_field.default_value:
                result["warnings"][schema_field.key] = "Advanced setting changed"

        return result

    def get_default_config(self) -> Dict[str, Any]:
        """Get default configuration from this schema.

        Returns:
            Default configuration dictionary.
        """
        return {sf.key: sf.default_value for sf in self.fields}

    def to_widget_definitions(self) -> List[Dict[str, Any]]:
        """Convert schema to widget definitions for UI.

        Returns:
            List of widget definition dictionaries.
        """
        widgets = []

        for schema_field in self.fields:
            widget_def: Dict[str, Any] = {
                "type": schema_field.widget_type.value,
                "label": schema_field.label,
                "config_path": f"plugins.{self.plugin_name}.{schema_field.key}",
                "help": schema_field.help_text,
                "category": schema_field.category,
                "advanced": schema_field.advanced,
                "requires_restart": schema_field.requires_restart,
            }

            # Add widget-specific options
            if schema_field.options:
                widget_def["options"] = schema_field.options

            if schema_field.min_value is not None:
                widget_def["min_value"] = schema_field.min_value

            if schema_field.max_value is not None:
                widget_def["max_value"] = schema_field.max_value

            if schema_field.step is not None:
                widget_def["step"] = schema_field.step

            if schema_field.placeholder:
                widget_def["placeholder"] = schema_field.placeholder

            if schema_field.file_extensions:
                widget_def["file_extensions"] = schema_field.file_extensions

            widgets.append(widget_def)

        return widgets


# Builder pattern for easier schema creation
class ConfigSchemaBuilder:
    """Builder for creating plugin configuration schemas."""

    def __init__(self, plugin_name: str, description: str = ""):
        """Initialize schema builder.

        Args:
            plugin_name: Name of the plugin.
            description: Plugin description.
        """
        self.schema = PluginConfigSchema(
            plugin_name=plugin_name, description=description
        )

    def add_checkbox(
        self,
        key: str,
        label: str,
        default: bool = False,
        help_text: str = "",
        category: str = "General",
        advanced: bool = False,
        requires_restart: bool = False,
    ) -> "ConfigSchemaBuilder":
        """Add a checkbox field."""
        field = ConfigField(
            key=key,
            widget_type=WidgetType.CHECKBOX,
            label=label,
            default_value=default,
            help_text=help_text,
            category=category,
            advanced=advanced,
            requires_restart=requires_restart,
            validation=ValidationRule(type=ValidationType.BOOLEAN),
        )
        self.schema.add_field(field)
        return self

    def add_slider(
        self,
        key: str,
        label: str,
        default: Union[int, float],
        min_value: Union[int, float],
        max_value: Union[int, float],
        step: Union[int, float] = 1,
        help_text: str = "",
        category: str = "General",
        advanced: bool = False,
        requires_restart: bool = False,
    ) -> "ConfigSchemaBuilder":
        """Add a slider field."""
        field = ConfigField(
            key=key,
            widget_type=WidgetType.SLIDER,
            label=label,
            default_value=default,
            help_text=help_text,
            category=category,
            advanced=advanced,
            requires_restart=requires_restart,
            min_value=min_value,
            max_value=max_value,
            step=step,
            validation=ValidationRule(
                type=(
                    ValidationType.INTEGER
                    if isinstance(default, int)
                    else ValidationType.FLOAT
                ),
                min_value=min_value,
                max_value=max_value,
            ),
        )
        self.schema.add_field(field)
        return self

    def add_text_input(
        self,
        key: str,
        label: str,
        default: str = "",
        placeholder: str = "",
        help_text: str = "",
        category: str = "General",
        validation_type: ValidationType = ValidationType.STRING,
        pattern: str = "",
        advanced: bool = False,
        requires_restart: bool = False,
    ) -> "ConfigSchemaBuilder":
        """Add a text input field."""
        validation = ValidationRule(
            type=validation_type, pattern=pattern if pattern else None
        )
        if pattern:
            validation.error_message = f"Value must match pattern: {pattern}"

        field = ConfigField(
            key=key,
            widget_type=WidgetType.TEXT_INPUT,
            label=label,
            default_value=default,
            help_text=help_text,
            category=category,
            advanced=advanced,
            requires_restart=requires_restart,
            placeholder=placeholder,
            validation=validation,
        )
        self.schema.add_field(field)
        return self

    def add_dropdown(
        self,
        key: str,
        label: str,
        options: List[str],
        default: Optional[str] = None,
        help_text: str = "",
        category: str = "General",
        advanced: bool = False,
        requires_restart: bool = False,
    ) -> "ConfigSchemaBuilder":
        """Add a dropdown field."""
        if default is None:
            default = options[0] if options else ""

        field = ConfigField(
            key=key,
            widget_type=WidgetType.DROPDOWN,
            label=label,
            default_value=default,
            help_text=help_text,
            category=category,
            advanced=advanced,
            requires_restart=requires_restart,
            options=options,
            validation=ValidationRule(
                type=ValidationType.STRING,
                custom_validator=lambda v: v in options,
                error_message=f"Value must be one of: {', '.join(options)}",
            ),
        )
        self.schema.add_field(field)
        return self

    def add_file_picker(
        self,
        key: str,
        label: str,
        default: str = "",
        extensions: Optional[List[str]] = None,
        help_text: str = "",
        category: str = "General",
        advanced: bool = False,
        requires_restart: bool = False,
    ) -> "ConfigSchemaBuilder":
        """Add a file picker field."""
        field = ConfigField(
            key=key,
            widget_type=WidgetType.FILE_PICKER,
            label=label,
            default_value=default,
            help_text=help_text,
            category=category,
            advanced=advanced,
            requires_restart=requires_restart,
            file_extensions=extensions or [],
            validation=ValidationRule(type=ValidationType.FILE_PATH),
        )
        self.schema.add_field(field)
        return self

    def build(self) -> PluginConfigSchema:
        """Build and return the schema.

        Returns:
            Complete plugin configuration schema.
        """
        return self.schema


# Utility functions for common field types
def create_enabled_field(
    category: str = "General", requires_restart: bool = False
) -> ConfigField:
    """Create a standard 'enabled' checkbox field."""
    return ConfigField(
        key="enabled",
        widget_type=WidgetType.CHECKBOX,
        label="Enabled",
        default_value=True,
        help_text="Enable or disable this plugin",
        category=category,
        requires_restart=requires_restart,
        validation=ValidationRule(type=ValidationType.BOOLEAN),
    )


def create_debug_field(category: str = "General") -> ConfigField:
    """Create a standard 'debug_logging' field."""
    return ConfigField(
        key="debug_logging",
        widget_type=WidgetType.CHECKBOX,
        label="Debug Logging",
        default_value=False,
        help_text="Enable detailed debug logging for this plugin",
        category=category,
        validation=ValidationRule(type=ValidationType.BOOLEAN),
    )
