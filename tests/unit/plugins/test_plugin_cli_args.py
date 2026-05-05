"""Tests for Plugin CLI Arguments Registration system."""

import argparse
import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from kollabor.cli import handle_early_plugin_args, parse_arguments
from kollabor_plugins.base import BasePlugin
from kollabor_plugins.discovery import PluginDiscovery


class TestPlugin(BasePlugin):
    """Test plugin for CLI argument registration."""

    @staticmethod
    def register_cli_args(parser: argparse.ArgumentParser) -> None:
        """Register custom CLI arguments."""
        group = parser.add_argument_group("Test Plugin")
        group.add_argument(
            "--test-arg", type=str, metavar="VALUE", help="Test argument for testing"
        )
        group.add_argument(
            "--test-flag", action="store_true", help="Test flag argument"
        )
        group.add_argument(
            "--test-number",
            type=int,
            default=42,
            help="Test number argument with default",
        )

    @staticmethod
    def handle_early_args(args: argparse.Namespace) -> bool:
        """Handle early exit arguments."""
        # Return True if --test-flag is set to test early exit
        if hasattr(args, "test_flag") and args.test_flag:
            return True
        return False


class TestEarlyExitPlugin(BasePlugin):
    """Test plugin that always requests early exit."""

    @staticmethod
    def register_cli_args(parser: argparse.ArgumentParser) -> None:
        """Register early exit argument."""
        group = parser.add_argument_group("Early Exit Test")
        group.add_argument(
            "--early-exit-test",
            action="store_true",
            help="Trigger early exit for testing",
        )

    @staticmethod
    def handle_early_args(args: argparse.Namespace) -> bool:
        """Handle early exit arguments."""
        if hasattr(args, "early_exit_test") and args.early_exit_test:
            return True
        return False


class TestBasePlugin(unittest.TestCase):
    """Test cases for BasePlugin CLI argument methods."""

    def test_base_plugin_register_cli_args_default(self):
        """Test that BasePlugin.register_cli_args() does nothing by default."""
        parser = argparse.ArgumentParser()
        BasePlugin.register_cli_args(parser)

        # Should have no custom arguments
        parser.parse_args([])
        # Just verify no exception raised

    def test_base_plugin_handle_early_args_default(self):
        """Test that BasePlugin.handle_early_args() returns False by default."""
        args = argparse.Namespace()
        result = BasePlugin.handle_early_args(args)
        self.assertFalse(result)

    def test_base_plugin_initialize_accepts_args(self):
        """Test that BasePlugin.initialize() accepts args parameter."""
        plugin = BasePlugin()
        args = argparse.Namespace(test_value="hello")

        # Should not raise exception
        plugin.initialize(args)

    def test_test_plugin_register_cli_args(self):
        """Test TestPlugin.register_cli_args() adds arguments."""
        parser = argparse.ArgumentParser()
        TestPlugin.register_cli_args(parser)

        # Parse with test arguments
        args = parser.parse_args(["--test-arg", "value1", "--test-flag"])

        self.assertEqual(args.test_arg, "value1")
        self.assertTrue(args.test_flag)
        self.assertEqual(args.test_number, 42)  # Default value

    def test_test_plugin_handle_early_args_no_flag(self):
        """Test TestPlugin.handle_early_args() without flag."""
        args = argparse.Namespace(test_flag=False)
        result = TestPlugin.handle_early_args(args)
        self.assertFalse(result)

    def test_test_plugin_handle_early_args_with_flag(self):
        """Test TestPlugin.handle_early_args() with flag set."""
        args = argparse.Namespace(test_flag=True)
        result = TestPlugin.handle_early_args(args)
        self.assertTrue(result)


class TestParseArgumentsWithPlugins(unittest.TestCase):
    """Test cases for parse_arguments() with plugin classes."""

    def test_parse_arguments_without_plugins(self):
        """Test parse_arguments() without any plugins."""
        args = parse_arguments(plugin_classes=[], argv=[])

        # Should have core arguments
        self.assertFalse(args.pipe)
        self.assertIsNone(args.query)

    def test_parse_arguments_with_test_plugin(self):
        """Test parse_arguments() with TestPlugin."""
        args = parse_arguments(
            plugin_classes=[TestPlugin], argv=["--test-arg", "myvalue"]
        )

        self.assertEqual(args.test_arg, "myvalue")
        self.assertFalse(args.test_flag)
        self.assertEqual(args.test_number, 42)

    def test_parse_arguments_with_multiple_plugins(self):
        """Test parse_arguments() with multiple plugins."""
        args = parse_arguments(
            plugin_classes=[TestPlugin, TestEarlyExitPlugin],
            argv=["--test-arg", "value", "--early-exit-test"],
        )

        self.assertEqual(args.test_arg, "value")
        self.assertTrue(args.early_exit_test)

    def test_parse_arguments_core_args_still_work(self):
        """Test that core arguments still work with plugin args."""
        args = parse_arguments(
            plugin_classes=[TestPlugin],
            argv=[
                "--test-arg",
                "value",
                "-a",
                "test-agent",
                "--profile",
                "test-profile",
            ],
        )

        self.assertEqual(args.test_arg, "value")
        self.assertEqual(args.agent, "test-agent")
        self.assertEqual(args.profile, "test-profile")


class TestHandleEarlyPluginArgs(unittest.TestCase):
    """Test cases for handle_early_plugin_args()."""

    def test_handle_early_args_no_plugins(self):
        """Test handle_early_plugin_args() with no plugins."""
        args = argparse.Namespace()
        result = handle_early_plugin_args(args, [])
        self.assertFalse(result)

    def test_handle_early_args_base_plugin(self):
        """Test handle_early_plugin_args() with BasePlugin."""
        args = argparse.Namespace()
        result = handle_early_plugin_args(args, [BasePlugin])
        self.assertFalse(result)

    def test_handle_early_args_test_plugin_no_flag(self):
        """Test handle_early_plugin_args() with TestPlugin without flag."""
        args = argparse.Namespace(test_flag=False)
        result = handle_early_plugin_args(args, [TestPlugin])
        self.assertFalse(result)

    def test_handle_early_args_test_plugin_with_flag(self):
        """Test handle_early_plugin_args() with TestPlugin with flag."""
        args = argparse.Namespace(test_flag=True)
        result = handle_early_plugin_args(args, [TestPlugin])
        self.assertTrue(result)

    def test_handle_early_args_early_exit_plugin(self):
        """Test handle_early_plugin_args() with TestEarlyExitPlugin."""
        args = argparse.Namespace(early_exit_test=True)
        result = handle_early_plugin_args(args, [TestEarlyExitPlugin])
        self.assertTrue(result)

    def test_handle_early_args_multiple_plugins(self):
        """Test handle_early_plugin_args() with multiple plugins."""
        args = argparse.Namespace(test_flag=False, early_exit_test=True)
        result = handle_early_plugin_args(args, [TestPlugin, TestEarlyExitPlugin])
        self.assertTrue(
            result
        )  # Should return True because TestEarlyExitPlugin returns True


class TestPluginDiscoveryClassesOnly(unittest.TestCase):
    """Test cases for PluginDiscovery.discover_classes_only()."""

    def test_discover_classes_only_returns_list(self):
        """Test that discover_classes_only() returns a list."""
        from pathlib import Path

        # Go up to project root: tests/unit/plugins -> project root
        plugins_dir = Path(__file__).parent.parent.parent.parent / "plugins"
        if not plugins_dir.exists():
            self.skipTest("Plugins directory not found")

        discovery = PluginDiscovery(plugins_dir)
        plugin_classes = discovery.discover_classes_only()

        self.assertIsInstance(plugin_classes, list)
        # Should find at least one plugin (hook_monitoring_plugin.py)
        self.assertGreater(len(plugin_classes), 0)

    def test_discover_classes_only_classes_have_required_methods(self):
        """Test that discovered classes have get_default_config method."""
        from pathlib import Path

        # Go up to project root: tests/unit/plugins -> project root
        plugins_dir = Path(__file__).parent.parent.parent.parent / "plugins"
        if not plugins_dir.exists():
            self.skipTest("Plugins directory not found")

        discovery = PluginDiscovery(plugins_dir)
        plugin_classes = discovery.discover_classes_only()

        for plugin_class in plugin_classes:
            # All discovered plugins should have get_default_config
            self.assertTrue(
                hasattr(plugin_class, "get_default_config"),
                f"{plugin_class.__name__} should have get_default_config",
            )


class TestPluginArgConflictDetection(unittest.TestCase):
    """Test cases for argument conflict detection."""

    def test_conflicting_args_raise_error(self):
        """Test that argparse raises error for conflicting argument names."""
        # This test verifies that argparse's default behavior
        # is to raise an error when two plugins try to register
        # the same argument name

        class Plugin1(BasePlugin):
            @staticmethod
            def register_cli_args(parser):
                group = parser.add_argument_group("Plugin1")
                group.add_argument("--conflict", help="First plugin arg")

        class Plugin2(BasePlugin):
            @staticmethod
            def register_cli_args(parser):
                group = parser.add_argument_group("Plugin2")
                group.add_argument("--conflict", help="Conflicting arg")

        # argparse by default raises an error for conflicting arguments
        parser = argparse.ArgumentParser()
        Plugin1.register_cli_args(parser)

        # This should raise an error
        with self.assertRaises(Exception):
            Plugin2.register_cli_args(parser)


class TestDiscoverPluginArgs(unittest.TestCase):
    """Test cases for discover_plugin_args()."""

    def test_discover_plugin_args_returns_tuple(self):
        """Test that discover_plugin_args() returns a tuple of (classes, discovery)."""
        from kollabor.cli import discover_plugin_args

        result = discover_plugin_args()
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        plugin_classes, discovery = result
        self.assertIsInstance(plugin_classes, list)

    def test_discover_plugin_args_finds_real_plugins(self):
        """Test that discover_plugin_args() finds actual plugins."""
        from kollabor.cli import discover_plugin_args

        plugin_classes, _discovery = discover_plugin_args()

        # Should find at least hook_monitoring_plugin
        plugin_names = [p.__name__ for p in plugin_classes]
        self.assertIn("HookMonitoringPlugin", plugin_names)


if __name__ == "__main__":
    unittest.main()
