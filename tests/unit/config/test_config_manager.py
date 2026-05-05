"""Tests for Config Manager functionality."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).parent.parent))

from kollabor_config import ConfigService  # noqa: E402


class TestConfigManager(unittest.TestCase):
    """Test cases for Config Manager."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = Path(self.temp_dir) / "test_config.json"

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir)

    def _make_service(self):
        """Create ConfigService with save path patched to temp dir."""
        with patch(
            "kollabor_config.loader.ConfigLoader._get_config_save_path",
            return_value=self.config_path,
        ):
            return ConfigService(self.config_path)

    def test_config_creation(self):
        """Test that default config is created and loaded."""
        with patch(
            "kollabor_config.loader.ConfigLoader._get_config_save_path",
            return_value=self.config_path,
        ):
            config_service = ConfigService(self.config_path)

        # Config file should be saved at the temp path
        self.assertTrue(self.config_path.exists())

        # Check that default values are present
        app_name = config_service.get("application.name")
        self.assertEqual(app_name, "Kollab")

    def test_dot_notation_access(self):
        """Test dot notation config access."""
        config_service = self._make_service()

        # Test getting nested values
        render_fps = config_service.get("terminal.render_fps")
        self.assertEqual(render_fps, 20)

        # Test default values
        missing_value = config_service.get("non.existent.key", "default")
        self.assertEqual(missing_value, "default")

    def test_config_modification(self):
        """Test config modification and persistence."""
        config_service = self._make_service()

        # Set a new value (in-memory)
        config_service.set("test.new_value", 123)

        # Verify it's set in-memory
        self.assertEqual(config_service.get("test.new_value"), 123)

        # Persist to disk
        with patch(
            "kollabor_config.loader.ConfigLoader._get_config_save_path",
            return_value=self.config_path,
        ):
            config_service.save_config()

        # Create new config service to test persistence
        config_service2 = self._make_service()
        self.assertEqual(config_service2.get("test.new_value"), 123)

    def test_config_merging(self):
        """Test config merging with existing files."""
        # Create initial config file
        initial_config = {
            "application": {"name": "Custom App", "custom_field": "custom_value"}
        }

        with open(self.config_path, "w") as f:
            json.dump(initial_config, f)

        # Load with config service (should merge with defaults)
        config_service = ConfigService(self.config_path)

        # Custom values should be preserved
        self.assertEqual(config_service.get("application.name"), "Custom App")
        self.assertEqual(config_service.get("application.custom_field"), "custom_value")

        # Default values should be added
        self.assertEqual(config_service.get("terminal.render_fps"), 20)


if __name__ == "__main__":
    unittest.main()
