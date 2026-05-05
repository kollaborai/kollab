"""Plugin SDK for creating custom Kollabor plugins.

Provides a framework for creating custom plugins that integrate with
the LLM core service, separate from MCP tools.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class KollaborPluginSDK:
    """SDK for creating and managing custom Kollabor plugins.

    Provides tools for plugin creation, validation, and registration,
    enabling developers to extend LLM functionality with custom tools.
    """

    # Plugin template
    PLUGIN_TEMPLATE = '''"""Custom Kollabor Plugin: {name}

{description}
"""

from typing import Any, Dict, List
from kollabor_events import Event, EventType, Hook, HookPriority


class {class_name}:
    """Custom plugin for {name}."""

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        """Get default configuration for this plugin."""
        return {{
            "plugins": {{
                "{plugin_id}": {{
                    "enabled": True,
                    "show_status": True,
                    # Add your configuration options here
                }}
            }}
        }}

    def __init__(self, name: str, event_bus, renderer, config):
        """Initialize the plugin."""
        self.name = name
        self.event_bus = event_bus
        self.renderer = renderer
        self.config = config

        # Initialize your plugin state here

    async def initialize(self):
        """Initialize plugin resources."""
        # Setup any async resources here
        pass

    async def register_hooks(self):
        """Register plugin hooks with event bus."""
        # Register your hooks here
        # Example:
        # hook = Hook(
        #     name="my_hook",
        #     plugin_name=self.name,
        #     event_type=EventType.USER_INPUT,
        #     priority=HookPriority.PREPROCESSING.value,
        #     callback=self._handle_input
        # )
        # await self.event_bus.register_hook(hook)
        pass

    def get_status_line(self) -> Dict[str, List[str]]:
        """Get status line for display."""
        return {{
            "A": [],  # Area A status items
            "B": [],  # Area B status items
            "C": []   # Area C status items
        }}

    async def shutdown(self):
        """Cleanup plugin resources."""
        # Cleanup any resources here
        pass

    # Add your plugin methods here
    async def process_command(self, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Process a custom command.

        Args:
            command: Command to execute
            params: Command parameters

        Returns:
            Command result
        """
        # Implement your command processing
        return {{"status": "success", "message": "Command processed"}}
'''

    def __init__(self):
        """Initialize the plugin SDK."""
        self.registered_plugins = {}
        self.custom_tools = {}
        self.plugin_validators = []

        # Plugin directory (created on demand, not at init)
        self.plugins_dir = Path.cwd() / "plugins"

        logger.info("Kollabor Plugin SDK initialized")

    def create_plugin_template(
        self,
        plugin_name: str,
        description: str = "A custom Kollabor plugin",
        output_dir: Optional[Path] = None,
    ) -> Path:
        """Generate a plugin template file.

        Args:
            plugin_name: Name of the plugin
            description: Plugin description
            output_dir: Directory to save the plugin (defaults to plugins/)

        Returns:
            Path to the created plugin file
        """
        # Sanitize plugin name
        plugin_id = plugin_name.lower().replace(" ", "_").replace("-", "_")
        class_name = (
            "".join(word.capitalize() for word in plugin_id.split("_")) + "Plugin"
        )

        # Generate plugin content
        plugin_content = self.PLUGIN_TEMPLATE.format(
            name=plugin_name,
            description=description,
            class_name=class_name,
            plugin_id=plugin_id,
        )

        # Determine output path
        if output_dir is None:
            output_dir = self.plugins_dir
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"{plugin_id}_plugin.py"

        # Write plugin file
        with open(output_path, "w") as f:
            f.write(plugin_content)

        logger.info(f"Created plugin template: {output_path}")
        return output_path

    def register_custom_tool(self, tool_definition: Dict[str, Any]) -> bool:
        """Register a custom tool for LLM use.

        Args:
            tool_definition: Tool definition including name, description, parameters

        Returns:
            True if registration successful
        """
        required_fields = ["name", "description", "handler"]
        if not all(field in tool_definition for field in required_fields):
            logger.error(f"Tool definition missing required fields: {required_fields}")
            return False

        tool_name = tool_definition["name"]

        # Validate handler is callable
        if not callable(tool_definition["handler"]):
            logger.error(f"Tool handler for '{tool_name}' is not callable")
            return False

        # Register the tool
        self.custom_tools[tool_name] = {
            "definition": tool_definition,
            "enabled": tool_definition.get("enabled", True),
            "plugin": tool_definition.get("plugin", "unknown"),
        }

        logger.info(f"Registered custom tool: {tool_name}")
        return True

    async def execute_custom_tool(
        self, tool_name: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a registered custom tool.

        Args:
            tool_name: Name of the tool to execute
            params: Tool parameters

        Returns:
            Tool execution result
        """
        if tool_name not in self.custom_tools:
            return {
                "error": f"Tool '{tool_name}' not found",
                "available_tools": list(self.custom_tools.keys()),
            }

        tool_info = self.custom_tools[tool_name]
        if not tool_info["enabled"]:
            return {"error": f"Tool '{tool_name}' is disabled"}

        try:
            handler = tool_info["definition"]["handler"]

            # Execute handler (async or sync)
            if asyncio.iscoroutinefunction(handler):
                result = await handler(params)
            else:
                result = handler(params)

            logger.info(f"Executed custom tool: {tool_name}")
            return {"status": "success", "result": result}

        except Exception as e:
            logger.error(f"Failed to execute custom tool {tool_name}: {e}")
            return {"error": str(e)}

    def validate_plugin(self, plugin_path: str) -> Dict[str, Any]:
        """Validate a plugin structure and security.

        Args:
            plugin_path: Path to the plugin file

        Returns:
            Validation result with any issues found
        """
        validation_result: Dict[str, Any] = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "info": [],
        }

        plugin_file = Path(plugin_path)

        # Check file exists
        if not plugin_file.exists():
            validation_result["valid"] = False
            validation_result["errors"].append(f"Plugin file not found: {plugin_path}")
            return validation_result

        # Check file extension
        if not plugin_file.suffix == ".py":
            validation_result["valid"] = False
            validation_result["errors"].append("Plugin must be a Python file (.py)")
            return validation_result

        try:
            # Read plugin content
            content = plugin_file.read_text()

            # Check for required methods
            required_methods = ["__init__", "get_default_config"]
            for method in required_methods:
                if f"def {method}" not in content:
                    validation_result["warnings"].append(
                        f"Missing recommended method: {method}"
                    )

            # Check for security issues
            dangerous_imports = [
                "os.system",
                "subprocess.call",
                "eval",
                "exec",
                "__import__",
            ]
            for dangerous in dangerous_imports:
                if dangerous in content:
                    validation_result["warnings"].append(
                        f"Potentially dangerous code: {dangerous}"
                    )

            # Check for proper class structure
            if "class " not in content:
                validation_result["errors"].append("No class definition found")
                validation_result["valid"] = False

            # Try to import and validate structure
            try:
                import importlib.util

                spec = importlib.util.spec_from_file_location(
                    "temp_plugin", plugin_file
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    # Find plugin class
                    plugin_class = None
                    for name in dir(module):
                        obj = getattr(module, name)
                        if isinstance(obj, type) and name.endswith("Plugin"):
                            plugin_class = obj
                            break

                    if plugin_class:
                        validation_result["info"].append(
                            f"Found plugin class: {plugin_class.__name__}"
                        )

                        # Check for required methods
                        if hasattr(plugin_class, "get_default_config"):
                            validation_result["info"].append(
                                "Has get_default_config method"
                            )
                    else:
                        validation_result["warnings"].append(
                            "No plugin class found (should end with 'Plugin')"
                        )

            except Exception as e:
                validation_result["errors"].append(f"Failed to import plugin: {e}")
                validation_result["valid"] = False

        except Exception as e:
            validation_result["errors"].append(f"Failed to read plugin file: {e}")
            validation_result["valid"] = False

        # Run custom validators
        for validator in self.plugin_validators:
            try:
                validator_result = validator(plugin_path)
                if validator_result:
                    validation_result["warnings"].extend(
                        validator_result.get("warnings", [])
                    )
                    validation_result["errors"].extend(
                        validator_result.get("errors", [])
                    )
            except Exception as e:
                logger.warning(f"Custom validator failed: {e}")

        return validation_result

    def add_validator(self, validator: Callable) -> None:
        """Add a custom plugin validator.

        Args:
            validator: Validation function that takes plugin path and returns issues
        """
        self.plugin_validators.append(validator)
        logger.debug("Added custom plugin validator")

    def list_custom_tools(self) -> List[Dict[str, Any]]:
        """List all registered custom tools.

        Returns:
            List of custom tools with their information
        """
        tools = []
        for tool_name, tool_info in self.custom_tools.items():
            tools.append(
                {
                    "name": tool_name,
                    "description": tool_info["definition"].get("description", ""),
                    "enabled": tool_info["enabled"],
                    "plugin": tool_info["plugin"],
                    "parameters": tool_info["definition"].get("parameters", {}),
                }
            )
        return tools

    def enable_tool(self, tool_name: str) -> bool:
        """Enable a custom tool.

        Args:
            tool_name: Name of the tool to enable

        Returns:
            True if tool was enabled
        """
        if tool_name in self.custom_tools:
            self.custom_tools[tool_name]["enabled"] = True
            logger.info(f"Enabled custom tool: {tool_name}")
            return True
        return False

    def disable_tool(self, tool_name: str) -> bool:
        """Disable a custom tool.

        Args:
            tool_name: Name of the tool to disable

        Returns:
            True if tool was disabled
        """
        if tool_name in self.custom_tools:
            self.custom_tools[tool_name]["enabled"] = False
            logger.info(f"Disabled custom tool: {tool_name}")
            return True
        return False

    def get_plugin_info(self, plugin_name: str) -> Optional[Dict[str, Any]]:
        """Get information about a registered plugin.

        Args:
            plugin_name: Name of the plugin

        Returns:
            Plugin information or None if not found
        """
        return self.registered_plugins.get(plugin_name)  # type: ignore[no-any-return]

    def register_plugin(self, plugin_name: str, plugin_info: Dict[str, Any]) -> bool:
        """Register a plugin with the SDK.

        Args:
            plugin_name: Name of the plugin
            plugin_info: Plugin information

        Returns:
            True if registration successful
        """
        self.registered_plugins[plugin_name] = plugin_info
        logger.info(f"Registered plugin with SDK: {plugin_name}")
        return True
