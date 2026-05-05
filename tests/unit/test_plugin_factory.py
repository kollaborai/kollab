"""Unit tests for PluginFactory component."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add the project root to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from kollabor_plugins.factory import PluginFactory


class TestPluginFactory(unittest.TestCase):
    """Test cases for PluginFactory component."""

    def setUp(self):
        """Set up test environment."""
        self.factory = PluginFactory()
        self.mock_event_bus = MagicMock()
        self.mock_renderer = MagicMock()
        self.mock_config = MagicMock()

    def test_initialization(self):
        """Test PluginFactory initialization."""
        self.assertEqual(self.factory.plugin_instances, {})
        self.assertEqual(self.factory.instantiation_errors, {})

    @patch("kollabor_plugins.factory.has_method")
    @patch("kollabor_plugins.factory.instantiate_plugin_safely")
    def test_instantiate_plugin_success(self, mock_instantiate, mock_has_method):
        """Test successful plugin instantiation."""
        mock_has_method.return_value = True
        mock_instance = MagicMock()
        mock_instantiate.return_value = mock_instance
        mock_plugin_class = MagicMock()

        result = self.factory.instantiate_plugin(
            mock_plugin_class,
            "TestPlugin",
            self.mock_event_bus,
            self.mock_renderer,
            self.mock_config,
        )

        self.assertEqual(result, mock_instance)
        self.assertIn("TestPlugin", self.factory.plugin_instances)
        self.assertEqual(self.factory.plugin_instances["TestPlugin"], mock_instance)
        self.assertNotIn("TestPlugin", self.factory.instantiation_errors)

    @patch("kollabor_plugins.factory.has_method")
    def test_instantiate_plugin_no_init(self, mock_has_method):
        """Test plugin instantiation when class has no __init__ method."""
        mock_has_method.return_value = False
        mock_plugin_class = MagicMock()

        result = self.factory.instantiate_plugin(
            mock_plugin_class,
            "TestPlugin",
            self.mock_event_bus,
            self.mock_renderer,
            self.mock_config,
        )

        self.assertIsNone(result)
        self.assertNotIn("TestPlugin", self.factory.plugin_instances)

    @patch("kollabor_plugins.factory.has_method")
    @patch("kollabor_plugins.factory.instantiate_plugin_safely")
    def test_instantiate_plugin_failure(self, mock_instantiate, mock_has_method):
        """Test plugin instantiation failure."""
        mock_has_method.return_value = True
        mock_instantiate.return_value = None
        mock_plugin_class = MagicMock()

        result = self.factory.instantiate_plugin(
            mock_plugin_class,
            "TestPlugin",
            self.mock_event_bus,
            self.mock_renderer,
            self.mock_config,
        )

        self.assertIsNone(result)
        self.assertNotIn("TestPlugin", self.factory.plugin_instances)
        self.assertIn("TestPlugin", self.factory.instantiation_errors)

    def test_instantiate_all(self):
        """Test instantiating all plugin classes."""
        # Mock plugin classes
        mock_class1 = MagicMock()
        mock_class2 = MagicMock()
        plugin_classes = {"Plugin1": mock_class1, "Plugin2": mock_class2}

        # Mock the instantiate_plugin method to return instances and update state
        with patch.object(self.factory, "instantiate_plugin") as mock_instantiate:
            mock_instance1 = MagicMock()
            mock_instance2 = MagicMock()

            def side_effect(plugin_class, plugin_name, *args):
                if plugin_name == "Plugin1":
                    self.factory.plugin_instances["Plugin1"] = mock_instance1
                    return mock_instance1
                elif plugin_name == "Plugin2":
                    self.factory.plugin_instances["Plugin2"] = mock_instance2
                    return mock_instance2
                return None

            mock_instantiate.side_effect = side_effect

            result = self.factory.instantiate_all(
                plugin_classes,
                self.mock_event_bus,
                self.mock_renderer,
                self.mock_config,
            )

            # Verify both plugins were attempted
            self.assertEqual(mock_instantiate.call_count, 2)

            expected_result = {"Plugin1": mock_instance1, "Plugin2": mock_instance2}
            self.assertEqual(result, expected_result)

    def test_get_instance(self):
        """Test getting plugin instance by name."""
        mock_instance = MagicMock()
        self.factory.plugin_instances["TestPlugin"] = mock_instance

        result = self.factory.get_instance("TestPlugin")
        self.assertEqual(result, mock_instance)

        # Test non-existent plugin
        result = self.factory.get_instance("NonExistent")
        self.assertIsNone(result)

    def test_get_all_instances(self):
        """Test getting all plugin instances."""
        mock_instances = {"Plugin1": MagicMock(), "Plugin2": MagicMock()}
        self.factory.plugin_instances = mock_instances

        result = self.factory.get_all_instances()
        self.assertEqual(result, mock_instances)

        # Verify it returns a copy
        result["Plugin3"] = MagicMock()
        self.assertNotIn("Plugin3", self.factory.plugin_instances)

    def test_get_instantiation_errors(self):
        """Test getting instantiation errors."""
        errors = {"Plugin1": "Error 1", "Plugin2": "Error 2"}
        self.factory.instantiation_errors = errors

        result = self.factory.get_instantiation_errors()
        self.assertEqual(result, errors)

        # Verify it returns a copy
        result["Plugin3"] = "Error 3"
        self.assertNotIn("Plugin3", self.factory.instantiation_errors)

    @patch("kollabor_plugins.factory.has_method")
    def test_initialize_plugin_no_method(self, mock_has_method):
        """Test initializing plugin with no initialize method."""
        mock_instance = MagicMock()
        self.factory.plugin_instances["TestPlugin"] = mock_instance
        mock_has_method.return_value = False

        result = self.factory.initialize_plugin("TestPlugin")
        self.assertTrue(result)

    @patch("kollabor_plugins.factory.has_method")
    @patch("kollabor_plugins.factory.safe_execute")
    def test_initialize_plugin_success(self, mock_safe_execute, mock_has_method):
        """Test successful plugin initialization."""
        mock_instance = MagicMock()
        self.factory.plugin_instances["TestPlugin"] = mock_instance
        mock_has_method.return_value = True
        mock_safe_execute.return_value = True

        result = self.factory.initialize_plugin("TestPlugin")
        self.assertTrue(result)
        mock_safe_execute.assert_called_once()

    def test_initialize_plugin_nonexistent(self):
        """Test initializing non-existent plugin."""
        result = self.factory.initialize_plugin("NonExistent")
        self.assertFalse(result)

    def test_initialize_all_plugins(self):
        """Test initializing all plugin instances."""
        mock_instance1 = MagicMock()
        mock_instance2 = MagicMock()
        self.factory.plugin_instances = {
            "Plugin1": mock_instance1,
            "Plugin2": mock_instance2,
        }

        with patch.object(self.factory, "initialize_plugin") as mock_initialize:
            mock_initialize.side_effect = [True, False]

            result = self.factory.initialize_all_plugins()

            self.assertEqual(result, {"Plugin1": True, "Plugin2": False})
            self.assertEqual(mock_initialize.call_count, 2)

    @patch("kollabor_plugins.factory.has_method")
    @patch("kollabor_plugins.factory.safe_execute")
    def test_shutdown_plugin_success(self, mock_safe_execute, mock_has_method):
        """Test successful plugin shutdown."""
        mock_instance = MagicMock()
        self.factory.plugin_instances["TestPlugin"] = mock_instance
        mock_has_method.return_value = True
        mock_safe_execute.return_value = True

        result = self.factory.shutdown_plugin("TestPlugin")
        self.assertTrue(result)
        mock_safe_execute.assert_called_once()

    def test_shutdown_plugin_nonexistent(self):
        """Test shutting down non-existent plugin."""
        result = self.factory.shutdown_plugin("NonExistent")
        self.assertFalse(result)

    def test_shutdown_all_plugins(self):
        """Test shutting down all plugin instances."""
        mock_instance1 = MagicMock()
        mock_instance2 = MagicMock()
        self.factory.plugin_instances = {
            "Plugin1": mock_instance1,
            "Plugin2": mock_instance2,
        }

        with patch.object(self.factory, "shutdown_plugin") as mock_shutdown:
            mock_shutdown.side_effect = [True, False]

            result = self.factory.shutdown_all_plugins()

            self.assertEqual(result, {"Plugin1": True, "Plugin2": False})
            self.assertEqual(mock_shutdown.call_count, 2)

    def test_get_factory_stats(self):
        """Test getting factory statistics."""
        mock_instance1 = MagicMock()
        mock_instance1.__class__.__name__ = "MockPlugin1"
        mock_instance2 = MagicMock()
        mock_instance2.__class__.__name__ = "MockPlugin2"

        self.factory.plugin_instances = {
            "Plugin1": mock_instance1,
            "Plugin2": mock_instance2,
        }
        self.factory.instantiation_errors = {"Plugin3": "Error"}

        stats = self.factory.get_factory_stats()

        self.assertEqual(stats["total_instances"], 2)
        self.assertEqual(stats["instantiation_errors"], 1)
        self.assertEqual(stats["plugin_names"], ["Plugin1", "Plugin2"])
        self.assertEqual(stats["error_plugins"], ["Plugin3"])
        self.assertEqual(
            stats["instance_types"],
            {"Plugin1": "MockPlugin1", "Plugin2": "MockPlugin2"},
        )


if __name__ == "__main__":
    unittest.main()
