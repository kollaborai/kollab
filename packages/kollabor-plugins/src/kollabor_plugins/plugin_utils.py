"""Plugin utility functions for introspection and safe method calling."""

import inspect
import logging
from typing import Any, Dict, List, Optional, Type

logger = logging.getLogger(__name__)


def has_method(obj: Any, method_name: str) -> bool:
    """Check if object has a callable method with given name.

    Args:
        obj: Object to check.
        method_name: Name of method to look for.

    Returns:
        True if object has callable method, False otherwise.

    Example:
        >>> class TestPlugin:
        ...     def get_status(self): pass
        >>> plugin = TestPlugin()
        >>> has_method(plugin, "get_status")
        True
        >>> has_method(plugin, "missing_method")
        False
    """
    return hasattr(obj, method_name) and callable(getattr(obj, method_name, None))


def safe_call_method(obj: Any, method_name: str, *args, **kwargs) -> Optional[Any]:
    """Safely call a method on an object with error handling.

    Args:
        obj: Object to call method on.
        method_name: Name of method to call.
        *args: Positional arguments to pass to method.
        **kwargs: Keyword arguments to pass to method.

    Returns:
        Method result or None if method doesn't exist or call failed.

    Example:
        >>> class TestPlugin:
        ...     def get_config(self, key="default"):
        ...         return {"key": key}
        >>> plugin = TestPlugin()
        >>> safe_call_method(plugin, "get_config", key="test")
        {"key": "test"}
    """
    if not has_method(obj, method_name):
        logger.debug(f"Object {type(obj).__name__} has no method '{method_name}'")
        return None

    try:
        method = getattr(obj, method_name)
        return method(*args, **kwargs)
    except Exception as e:
        logger.error(f"Failed to call {type(obj).__name__}.{method_name}: {e}")
        return None


def get_plugin_metadata(plugin_class: Type) -> Dict[str, Any]:
    """Extract metadata from a plugin class.

    Args:
        plugin_class: Plugin class to analyze.

    Returns:
        Dictionary with plugin metadata.

    Example:
        >>> class TestPlugin:
        ...     '''Test plugin for demo.'''
        ...     VERSION = "1.0.0"
        ...     def get_default_config(self): return {}
        >>> get_plugin_metadata(TestPlugin)
        {
            "name": "TestPlugin",
            "docstring": "Test plugin for demo.",
            "version": "1.0.0",
            "has_config": True,
            "methods": ["get_default_config"]
        }
    """
    metadata: Dict[str, Any] = {
        "name": plugin_class.__name__,
        "docstring": inspect.getdoc(plugin_class) or "",
        "version": getattr(plugin_class, "VERSION", "unknown"),
        "has_config": has_method(plugin_class, "get_default_config"),
        "has_startup_info": has_method(plugin_class, "get_startup_info"),
        "has_status": False,
        "methods": [],
    }

    # Check for common plugin methods
    common_methods = [
        "initialize",
        "register_hooks",
        "shutdown",
        "get_default_config",
        "get_startup_info",
        "get_status_line",
        "get_status_lines",
    ]

    for method_name in common_methods:
        if has_method(plugin_class, method_name):
            metadata["methods"].append(method_name)
            if method_name.startswith("get_status"):
                metadata["has_status"] = True

    return metadata


def validate_plugin_interface(
    plugin_class: Type, required_methods: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Validate that plugin class implements required interface.

    Args:
        plugin_class: Plugin class to validate.
        required_methods: List of required method names.

    Returns:
        Dictionary with validation results.

    Example:
        >>> class TestPlugin:
        ...     def initialize(self): pass
        >>> validate_plugin_interface(TestPlugin, ["initialize", "shutdown"])
        {
            "valid": False,
            "missing_methods": ["shutdown"],
            "has_methods": ["initialize"],
            "errors": []
        }
    """
    if required_methods is None:
        required_methods = ["initialize", "register_hooks", "shutdown"]

    result: Dict[str, Any] = {
        "valid": True,
        "missing_methods": [],
        "has_methods": [],
        "errors": [],
    }

    # Check for required methods
    for method_name in required_methods:
        if has_method(plugin_class, method_name):
            result["has_methods"].append(method_name)
        else:
            result["missing_methods"].append(method_name)
            result["valid"] = False

    # Check constructor signature
    try:
        init_signature = inspect.signature(plugin_class.__init__)
        params = list(init_signature.parameters.keys())

        # Skip 'self' parameter
        if params and params[0] == "self":
            params = params[1:]

        # Common expected parameters for plugins
        expected_params = ["name", "event_bus", "renderer", "config"]
        missing_params = [p for p in expected_params if p not in params]

        if missing_params:
            result["errors"].append(f"Constructor missing parameters: {missing_params}")

    except Exception as e:
        result["errors"].append(f"Failed to inspect constructor: {e}")

    return result


def get_plugin_config_safely(plugin_class: Type) -> Dict[str, Any]:
    """Safely get default configuration from plugin class.

    Args:
        plugin_class: Plugin class to get config from.

    Returns:
        Plugin's default configuration or empty dict if unavailable.
    """
    try:
        if has_method(plugin_class, "get_default_config"):
            config = plugin_class.get_default_config()
            if isinstance(config, dict):
                return config
            else:
                logger.warning(
                    f"Plugin {plugin_class.__name__} returned non-dict config: {type(config)}"
                )
        else:
            logger.debug(
                f"Plugin {plugin_class.__name__} has no get_default_config method"
            )
    except Exception as e:
        logger.error(f"Failed to get config from plugin {plugin_class.__name__}: {e}")

    return {}


def instantiate_plugin_safely(plugin_class: Type, **kwargs) -> Optional[Any]:
    """Safely instantiate a plugin with error handling.

    Args:
        plugin_class: Plugin class to instantiate.
        **kwargs: Arguments to pass to plugin constructor.

    Returns:
        Plugin instance or None if instantiation failed.
    """
    try:
        # Validate that this looks like a plugin class
        if not plugin_class.__name__.endswith("Plugin"):
            logger.warning(
                f"Class {plugin_class.__name__} doesn't follow plugin naming convention"
            )

        # Try to instantiate
        instance = plugin_class(**kwargs)
        logger.info(f"Successfully instantiated plugin: {plugin_class.__name__}")
        return instance

    except Exception as e:
        logger.error(f"Failed to instantiate plugin {plugin_class.__name__}: {e}")
        return None


def collect_plugin_status_safely(
    plugin_instance: Any, plugin_name: str
) -> Dict[str, List[str]]:
    """Safely collect status information from plugin instance.

    Args:
        plugin_instance: Plugin instance to get status from.
        plugin_name: Name of plugin (for logging).

    Returns:
        Dictionary with status areas A, B, C containing lists of status lines.
    """
    status_areas: Dict[str, Any] = {"A": [], "B": [], "C": []}

    try:
        # Try new format first: get_status_lines() returning dict
        if has_method(plugin_instance, "get_status_lines"):
            plugin_status = safe_call_method(plugin_instance, "get_status_lines")

            if isinstance(plugin_status, dict):
                # New format: plugin returns dict with areas
                for area, lines in plugin_status.items():
                    if area in status_areas:
                        if isinstance(lines, list):
                            status_areas[area].extend(
                                [line for line in lines if line and line.strip()]
                            )
                        elif isinstance(lines, str) and lines.strip():
                            status_areas[area].append(lines.strip())
                return status_areas
            elif isinstance(plugin_status, list):
                # Legacy format: plugin returns list, put in area A
                status_areas["A"].extend(
                    [line for line in plugin_status if line and line.strip()]
                )
                return status_areas
            elif isinstance(plugin_status, str) and plugin_status.strip():
                # Legacy format: plugin returns string, put in area A
                status_areas["A"].append(plugin_status.strip())
                return status_areas

        # Try legacy format: get_status_line() returning string
        if has_method(plugin_instance, "get_status_line"):
            plugin_status = safe_call_method(plugin_instance, "get_status_line")
            if isinstance(plugin_status, str) and plugin_status.strip():
                status_areas["A"].append(plugin_status.strip())

    except Exception as e:
        logger.warning(f"Failed to get status from plugin {plugin_name}: {e}")

    return status_areas
