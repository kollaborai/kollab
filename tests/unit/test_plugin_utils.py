"""Tests for plugin utility functions."""

import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

from kollabor_plugins.plugin_utils import (
    collect_plugin_status_safely,
    get_plugin_config_safely,
    get_plugin_metadata,
    has_method,
    instantiate_plugin_safely,
    safe_call_method,
    validate_plugin_interface,
)


class TestPluginUtils(unittest.TestCase):
    """Test cases for plugin utilities."""

    def setUp(self):
        """Set up test fixtures."""

        # Create test plugin classes
        class ValidTestPlugin:
            """Test plugin with all required methods."""

            VERSION = "1.0.0"

            def __init__(self, name, event_bus, renderer, config, extra=None):
                self.name = name
                self.config = config

            def initialize(self):
                pass

            def register_hooks(self):
                pass

            def shutdown(self):
                pass

            @staticmethod
            def get_default_config():
                return {"test": {"value": 123}}

            @staticmethod
            def get_startup_info(config):
                return ["Plugin initialized successfully"]

            def get_status_lines(self):
                return {"A": ["Status line 1"], "B": ["Status line 2"]}

        class MinimalPlugin:
            """Minimal plugin with only required methods."""

            def __init__(self, name, event_bus, renderer, config, extra=None):
                pass

            def initialize(self):
                pass

            def register_hooks(self):
                pass

            def shutdown(self):
                pass

        class BrokenPlugin:
            """Plugin with broken methods."""

            def __init__(self, name, event_bus, renderer, config, extra=None):
                pass

            def initialize(self):
                raise Exception("Initialization failed")

            @staticmethod
            def get_default_config():
                return "not_a_dict"  # Invalid return type

        class LegacyPlugin:
            """Plugin using legacy status format."""

            def __init__(self):
                pass

            def get_status_line(self):
                return "Legacy status"

        self.ValidTestPlugin = ValidTestPlugin
        self.MinimalPlugin = MinimalPlugin
        self.BrokenPlugin = BrokenPlugin
        self.LegacyPlugin = LegacyPlugin

    def test_has_method(self):
        """Test method existence checking."""
        plugin = self.ValidTestPlugin("test", None, None, None, {})

        # Should find existing methods
        self.assertTrue(has_method(plugin, "initialize"))
        self.assertTrue(has_method(plugin, "get_status_lines"))

        # Should not find missing methods
        self.assertFalse(has_method(plugin, "missing_method"))

        # Should not find non-callable attributes
        self.assertFalse(has_method(plugin, "name"))  # attribute, not method

    def test_safe_call_method(self):
        """Test safe method calling."""
        plugin = self.ValidTestPlugin("test", None, None, None, {})

        # Call existing method
        result = safe_call_method(plugin, "get_status_lines")
        self.assertEqual(result, {"A": ["Status line 1"], "B": ["Status line 2"]})

        # Call missing method
        result = safe_call_method(plugin, "missing_method")
        self.assertIsNone(result)

        # Call static method
        result = safe_call_method(self.ValidTestPlugin, "get_default_config")
        self.assertEqual(result, {"test": {"value": 123}})

    def test_get_plugin_metadata(self):
        """Test plugin metadata extraction."""
        metadata = get_plugin_metadata(self.ValidTestPlugin)

        expected_keys = [
            "name",
            "docstring",
            "version",
            "has_config",
            "has_startup_info",
            "has_status",
            "methods",
        ]
        for key in expected_keys:
            self.assertIn(key, metadata)

        self.assertEqual(metadata["name"], "ValidTestPlugin")
        self.assertEqual(metadata["version"], "1.0.0")
        self.assertTrue(metadata["has_config"])
        self.assertTrue(metadata["has_startup_info"])
        self.assertTrue(metadata["has_status"])

        expected_methods = [
            "initialize",
            "register_hooks",
            "shutdown",
            "get_default_config",
            "get_startup_info",
            "get_status_lines",
        ]
        for method in expected_methods:
            self.assertIn(method, metadata["methods"])

    def test_validate_plugin_interface(self):
        """Test plugin interface validation."""
        # Valid plugin should pass
        result = validate_plugin_interface(self.ValidTestPlugin)
        self.assertTrue(result["valid"])
        self.assertEqual(result["missing_methods"], [])

        # Minimal plugin should pass with default requirements
        result = validate_plugin_interface(self.MinimalPlugin)
        self.assertTrue(result["valid"])

        # Test with custom requirements
        result = validate_plugin_interface(
            self.MinimalPlugin, ["initialize", "missing_method"]
        )
        self.assertFalse(result["valid"])
        self.assertIn("missing_method", result["missing_methods"])
        self.assertIn("initialize", result["has_methods"])

    def test_get_plugin_config_safely(self):
        """Test safe plugin config retrieval."""
        # Valid config
        config = get_plugin_config_safely(self.ValidTestPlugin)
        self.assertEqual(config, {"test": {"value": 123}})

        # Plugin without config method
        config = get_plugin_config_safely(self.MinimalPlugin)
        self.assertEqual(config, {})

        # Plugin with broken config method
        config = get_plugin_config_safely(self.BrokenPlugin)
        self.assertEqual(config, {})

    def test_instantiate_plugin_safely(self):
        """Test safe plugin instantiation."""
        # Valid plugin
        plugin = instantiate_plugin_safely(
            self.ValidTestPlugin, name="test", event_bus=None, renderer=None, config={}
        )
        self.assertIsInstance(plugin, self.ValidTestPlugin)

        # Plugin with wrong constructor signature
        plugin = instantiate_plugin_safely(self.LegacyPlugin)
        self.assertIsInstance(plugin, self.LegacyPlugin)

        # Plugin that raises exception in constructor
        class FailingPlugin:
            def __init__(self):
                raise Exception("Constructor failed")

        plugin = instantiate_plugin_safely(FailingPlugin)
        self.assertIsNone(plugin)

    def test_collect_plugin_status_safely(self):
        """Test safe plugin status collection."""
        # Plugin with new status format (dict)
        plugin = self.ValidTestPlugin("test", None, None, None, {})
        status = collect_plugin_status_safely(plugin, "ValidTestPlugin")

        expected = {"A": ["Status line 1"], "B": ["Status line 2"], "C": []}
        self.assertEqual(status, expected)

        # Plugin with legacy status format (string)
        plugin = self.LegacyPlugin()
        status = collect_plugin_status_safely(plugin, "LegacyPlugin")

        expected = {"A": ["Legacy status"], "B": [], "C": []}
        self.assertEqual(status, expected)

        # Plugin without status method
        plugin = self.MinimalPlugin("test", None, None, None, {})
        status = collect_plugin_status_safely(plugin, "MinimalPlugin")

        expected = {"A": [], "B": [], "C": []}
        self.assertEqual(status, expected)

    def test_collect_plugin_status_filters_empty(self):
        """Test that status collection filters out empty strings."""

        class StatusPlugin:
            def get_status_lines(self):
                return {
                    "A": ["Valid line", "", "  ", "Another valid"],
                    "B": ["", "   "],
                    "C": ["Only valid line"],
                }

        plugin = StatusPlugin()
        status = collect_plugin_status_safely(plugin, "StatusPlugin")

        expected = {
            "A": ["Valid line", "Another valid"],
            "B": [],
            "C": ["Only valid line"],
        }
        self.assertEqual(status, expected)


if __name__ == "__main__":
    unittest.main()
