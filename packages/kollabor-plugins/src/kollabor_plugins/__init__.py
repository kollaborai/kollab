"""Kollabor plugin infrastructure - discovery, registry, factory, and base classes."""

from .base import BasePlugin
from .collector import PluginStatusCollector
from .discovery import PluginDiscovery
from .factory import PluginFactory
from .plugin_sdk import KollaborPluginSDK
from .plugin_utils import (
    collect_plugin_status_safely,
    get_plugin_config_safely,
    get_plugin_metadata,
    has_method,
    instantiate_plugin_safely,
    safe_call_method,
    validate_plugin_interface,
)
from .registry import PluginRegistry

__all__ = [
    "BasePlugin",
    "PluginDiscovery",
    "PluginFactory",
    "PluginRegistry",
    "PluginStatusCollector",
    "KollaborPluginSDK",
    "has_method",
    "safe_call_method",
    "get_plugin_metadata",
    "validate_plugin_interface",
    "get_plugin_config_safely",
    "instantiate_plugin_safely",
    "collect_plugin_status_safely",
]
