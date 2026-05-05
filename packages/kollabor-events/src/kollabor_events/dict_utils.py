"""Dictionary utility functions for configuration and data manipulation."""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def deep_merge(target: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge source dictionary into target dictionary.

    Recursively merges dictionaries, with source values taking precedence.
    Non-dict values in source will overwrite target values.

    Args:
        target: Target dictionary to merge into (not modified).
        source: Source dictionary to merge from.

    Returns:
        New dictionary with merged values.

    Example:
        >>> target = {"a": {"b": 1, "c": 2}, "d": 3}
        >>> source = {"a": {"b": 10, "e": 4}, "f": 5}
        >>> deep_merge(target, source)
        {"a": {"b": 10, "c": 2, "e": 4}, "d": 3, "f": 5}
    """
    if not isinstance(target, dict):
        logger.warning(f"Target is not a dict: {type(target)}")
        return source if isinstance(source, dict) else {}

    if not isinstance(source, dict):
        logger.warning(f"Source is not a dict: {type(source)}")
        return target.copy()

    result = target.copy()

    for key, value in source.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # Recursively merge nested dictionaries
            result[key] = deep_merge(result[key], value)
        else:
            # Overwrite with source value
            result[key] = value

    return result


def safe_get(data: Dict[str, Any], key_path: str, default: Any = None) -> Any:
    """Safely get a value from nested dictionary using dot notation.

    Args:
        data: Dictionary to search in.
        key_path: Dot-separated path to the value (e.g., "section.subsection.key").
        default: Default value if key not found.

    Returns:
        Value at key_path or default if not found.

    Example:
        >>> data = {"terminal": {"render_fps": 20}}
        >>> safe_get(data, "terminal.render_fps")
        20
        >>> safe_get(data, "missing.key", "fallback")
        "fallback"
    """
    if not isinstance(data, dict):
        return default

    if not key_path:
        return default

    keys = key_path.split(".")
    current = data

    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]

    return current


def safe_set(data: Dict[str, Any], key_path: str, value: Any) -> bool:
    """Safely set a value in nested dictionary using dot notation.

    Creates intermediate dictionaries as needed.

    Args:
        data: Dictionary to modify (modified in place).
        key_path: Dot-separated path to set (e.g., "section.subsection.key").
        value: Value to set.

    Returns:
        True if successful, False otherwise.

    Example:
        >>> data = {}
        >>> safe_set(data, "terminal.render_fps", 30)
        True
        >>> data
        {"terminal": {"render_fps": 30}}
    """
    if not isinstance(data, dict):
        logger.error(f"Cannot set key in non-dict: {type(data)}")
        return False

    if not key_path:
        logger.error("Empty key_path provided")
        return False

    try:
        keys = key_path.split(".")
        current = data

        # Navigate to parent of target key, creating dicts as needed
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            elif not isinstance(current[key], dict):
                logger.warning(f"Overwriting non-dict value at key '{key}'")
                current[key] = {}
            current = current[key]

        # Set the final value
        current[keys[-1]] = value
        return True

    except Exception as e:
        logger.error(f"Failed to set key '{key_path}': {e}")
        return False


def merge_multiple(configs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge multiple dictionaries in order.

    Args:
        configs: List of dictionaries to merge (later configs take precedence).

    Returns:
        Merged dictionary.

    Example:
        >>> configs = [{"a": 1}, {"b": 2}, {"a": 10}]
        >>> merge_multiple(configs)
        {"a": 10, "b": 2}
    """
    if not configs:
        return {}

    result: Dict[str, Any] = {}
    for config in configs:
        if isinstance(config, dict):
            result = deep_merge(result, config)  # type: ignore[assignment]
        else:
            logger.warning(f"Skipping non-dict config: {type(config)}")

    return result


def flatten_dict(
    data: Dict[str, Any], prefix: str = "", separator: str = "."
) -> Dict[str, Any]:
    """Flatten nested dictionary to dot-notation keys.

    Args:
        data: Dictionary to flatten.
        prefix: Prefix for keys (used internally for recursion).
        separator: Separator for nested keys.

    Returns:
        Flattened dictionary.

    Example:
        >>> data = {"a": {"b": 1, "c": 2}, "d": 3}
        >>> flatten_dict(data)
        {"a.b": 1, "a.c": 2, "d": 3}
    """
    result = {}

    for key, value in data.items():
        new_key = f"{prefix}{separator}{key}" if prefix else key

        if isinstance(value, dict):
            result.update(flatten_dict(value, new_key, separator))
        else:
            result[new_key] = value

    return result


def unflatten_dict(data: Dict[str, Any], separator: str = ".") -> Dict[str, Any]:
    """Unflatten dot-notation keys to nested dictionary.

    Args:
        data: Dictionary with dot-notation keys.
        separator: Separator used in keys.

    Returns:
        Nested dictionary.

    Example:
        >>> data = {"a.b": 1, "a.c": 2, "d": 3}
        >>> unflatten_dict(data)
        {"a": {"b": 1, "c": 2}, "d": 3}
    """
    result: Dict[str, Any] = {}

    for key, value in data.items():
        safe_set(result, key.replace(separator, "."), value)

    return result
