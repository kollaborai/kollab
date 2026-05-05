"""Tests for Plugin Registry functionality."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from kollabor_plugins import PluginRegistry


class TestPluginRegistry(unittest.TestCase):
    """Test cases for Plugin Registry."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.plugins_dir = Path(self.temp_dir) / "plugins"
        self.plugins_dir.mkdir()

        # Create a simple test plugin
        test_plugin_content = """
class TestPlugin:
    @staticmethod
    def get_default_config():
        return {
            "plugins": {
                "test": {
                    "enabled": True,
                    "test_value": 42
                }
            }
        }

    @staticmethod
    def get_startup_info(config):
        return ["Test plugin loaded"]
"""
        with open(self.plugins_dir / "test_plugin.py", "w") as f:
            f.write(test_plugin_content)

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir)

    def test_plugin_discovery(self):
        """Test that plugins are discovered correctly."""
        registry = PluginRegistry(self.plugins_dir)
        discovered = registry.discover_plugins()

        self.assertIn("test_plugin", discovered)
        self.assertEqual(len(discovered), 1)

    @unittest.skip(
        "Temp plugin import path issue - use actual plugins dir test instead"
    )
    def test_plugin_loading(self):
        """Test that plugins are loaded correctly."""
        registry = PluginRegistry(self.plugins_dir)
        registry.load_all_plugins()

        plugins = registry.list_plugins()
        self.assertIn("TestPlugin", plugins)

    def test_config_merging(self):
        """Test that plugin configs are merged correctly."""
        # Go up to project root: tests/unit/plugins -> project root
        actual_plugins_dir = Path(__file__).parent.parent.parent.parent / "plugins"
        if not actual_plugins_dir.exists():
            self.skipTest("Plugins directory not found")
        registry = PluginRegistry(actual_plugins_dir)
        registry.load_all_plugins()

        merged_config = registry.get_merged_config()

        # Test that we have a plugins section
        self.assertIn("plugins", merged_config)

        # Test that the actual plugins have their configs
        if "test" in merged_config["plugins"]:
            self.assertIn("enabled", merged_config["plugins"]["test"])

        if "llm" in merged_config["plugins"]:
            self.assertIn("api_url", merged_config["plugins"]["llm"])


if __name__ == "__main__":
    unittest.main()
