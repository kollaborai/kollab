"""Unit tests for HookAdapter module.

Tests cover:
    - Plugin instance tracking and lifecycle
    - Old format to new format conversion
    - New format passthrough
    - Version detection (auto-detect old vs new)
    - Plugin instance injection during execution
    - Backward compatibility
    - Statistics tracking
"""

import unittest
from unittest.mock import AsyncMock

from kollabor_events.hook_adapter import HookAdapter
from kollabor_events.models import EventType, Hook


class MockPlugin:
    """Mock plugin instance for testing."""

    def __init__(self, name: str):
        self.name = name


class OldFormatHook:
    """Mock old format hook with plugin object reference."""

    def __init__(
        self, name: str, plugin: any, event_type: EventType, priority: int, callback
    ):
        self.name = name
        self.plugin = plugin
        self.event_type = event_type
        self.priority = priority
        self.callback = callback
        self.enabled = True


class TestHookAdapterPluginTracking(unittest.TestCase):
    """Test plugin instance tracking functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.adapter = HookAdapter()
        self.plugin = MockPlugin("test_plugin")

    def test_register_plugin(self):
        """Test registering a plugin instance."""
        self.adapter.register_plugin("test_plugin", self.plugin)

        instance = self.adapter.get_plugin_instance("test_plugin")
        self.assertIs(instance, self.plugin)

    def test_register_multiple_plugins(self):
        """Test registering multiple plugin instances."""
        plugin1 = MockPlugin("plugin1")
        plugin2 = MockPlugin("plugin2")

        self.adapter.register_plugin("plugin1", plugin1)
        self.adapter.register_plugin("plugin2", plugin2)

        self.assertIs(self.adapter.get_plugin_instance("plugin1"), plugin1)
        self.assertIs(self.adapter.get_plugin_instance("plugin2"), plugin2)

    def test_register_plugin_empty_name_raises(self):
        """Test that registering with empty name raises ValueError."""
        with self.assertRaises(ValueError) as context:
            self.adapter.register_plugin("", self.plugin)

        self.assertIn("plugin_name cannot be empty", str(context.exception))

    def test_register_plugin_none_instance_raises(self):
        """Test that registering None instance raises ValueError."""
        with self.assertRaises(ValueError) as context:
            self.adapter.register_plugin("test_plugin", None)

        self.assertIn("plugin_instance cannot be None", str(context.exception))

    def test_unregister_plugin(self):
        """Test unregistering a plugin instance."""
        self.adapter.register_plugin("test_plugin", self.plugin)
        result = self.adapter.unregister_plugin("test_plugin")

        self.assertTrue(result)
        self.assertIsNone(self.adapter.get_plugin_instance("test_plugin"))

    def test_unregister_nonexistent_plugin(self):
        """Test unregistering a non-existent plugin returns False."""
        result = self.adapter.unregister_plugin("nonexistent")

        self.assertFalse(result)

    def test_get_stats_updates_after_registration(self):
        """Test that statistics update after plugin registration."""
        stats_before = self.adapter.get_stats()
        self.assertEqual(stats_before["plugins_tracked"], 0)

        self.adapter.register_plugin("test_plugin", self.plugin)

        stats_after = self.adapter.get_stats()
        self.assertEqual(stats_after["plugins_tracked"], 1)


class TestHookAdapterFormatConversion(unittest.TestCase):
    """Test hook format conversion (old to new)."""

    def setUp(self):
        """Set up test fixtures."""
        self.adapter = HookAdapter()
        self.plugin = MockPlugin("test_plugin")
        self.adapter.register_plugin("test_plugin", self.plugin)

        # Create callback
        self.callback = AsyncMock(return_value=None)

    def test_convert_old_format_hook(self):
        """Test converting old format hook to new format."""
        old_hook = OldFormatHook(
            name="test_hook",
            plugin=self.plugin,
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=self.callback,
        )

        adapted = self.adapter.adapt_hook_for_registration(old_hook)

        # Verify new format
        self.assertIsInstance(adapted, Hook)
        self.assertEqual(adapted.name, "test_hook")
        self.assertEqual(adapted.plugin_name, "test_plugin")
        self.assertEqual(adapted.event_type, EventType.USER_INPUT)
        self.assertEqual(adapted.priority, 100)
        self.assertIs(adapted.callback, self.callback)

    def test_convert_old_format_auto_registers_plugin(self):
        """Test that old format conversion auto-registers plugin."""
        # Create new plugin without registering
        new_plugin = MockPlugin("new_plugin")
        adapter = HookAdapter()  # Fresh adapter without plugin registered

        old_hook = OldFormatHook(
            name="test_hook",
            plugin=new_plugin,
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=self.callback,
        )

        adapted = adapter.adapt_hook_for_registration(old_hook)

        # Verify plugin was auto-registered
        self.assertEqual(adapted.plugin_name, "new_plugin")
        self.assertIsNotNone(adapter.get_plugin_instance("new_plugin"))

    def test_new_format_passthrough(self):
        """Test that new format hooks pass through unchanged."""
        new_hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=self.callback,
        )

        adapted = self.adapter.adapt_hook_for_registration(new_hook)

        # Should be same object
        self.assertIs(adapted, new_hook)

    def test_conversion_increments_stats(self):
        """Test that conversion increments statistics."""
        old_hook = OldFormatHook(
            name="test_hook",
            plugin=self.plugin,
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=self.callback,
        )

        self.adapter.adapt_hook_for_registration(old_hook)

        stats = self.adapter.get_stats()
        self.assertEqual(stats["hooks_registered"], 1)
        self.assertEqual(stats["hooks_converted"], 1)

    def test_new_format_increments_registered_only(self):
        """Test that new format increments hooks_registered only."""
        new_hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=self.callback,
        )

        self.adapter.adapt_hook_for_registration(new_hook)

        stats = self.adapter.get_stats()
        self.assertEqual(stats["hooks_registered"], 1)
        self.assertEqual(stats["hooks_converted"], 0)

    def test_old_format_plugin_without_name_raises(self):
        """Test that old format hook with unnamed plugin raises ValueError."""

        # Create plugin without name attribute
        class NamelessPlugin:
            pass

        nameless_plugin = NamelessPlugin()

        old_hook = OldFormatHook(
            name="test_hook",
            plugin=nameless_plugin,
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=self.callback,
        )

        with self.assertRaises(ValueError) as context:
            self.adapter.adapt_hook_for_registration(old_hook)

        self.assertIn("Cannot determine plugin name", str(context.exception))


class TestHookAdapterVersionDetection(unittest.TestCase):
    """Test automatic version detection (old vs new format)."""

    def setUp(self):
        """Set up test fixtures."""
        self.adapter = HookAdapter()
        self.plugin = MockPlugin("test_plugin")
        self.callback = AsyncMock(return_value=None)

    def test_detect_old_format_by_plugin_attribute(self):
        """Test detecting old format by presence of plugin attribute."""
        old_hook = OldFormatHook(
            name="test_hook",
            plugin=self.plugin,
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=self.callback,
        )

        adapted = self.adapter.adapt_hook_for_registration(old_hook)

        # Should convert to new format
        self.assertIsInstance(adapted, Hook)
        self.assertEqual(adapted.plugin_name, "test_plugin")

    def test_detect_new_format_by_plugin_name_attribute(self):
        """Test detecting new format by presence of plugin_name attribute."""
        new_hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=self.callback,
        )

        adapted = self.adapter.adapt_hook_for_registration(new_hook)

        # Should pass through unchanged
        self.assertIs(adapted, new_hook)

    def test_unknown_format_raises_error(self):
        """Test that unknown format raises ValueError."""

        # Create a hook-like object without proper attributes
        class InvalidHook:
            def __init__(self):
                self.invalid_attr = "value"

        invalid_hook = InvalidHook()

        with self.assertRaises(ValueError) as context:
            self.adapter.adapt_hook_for_registration(invalid_hook)

        self.assertIn("has neither 'plugin_name'", str(context.exception))


class TestHookAdapterExecutionInjection(unittest.TestCase):
    """Test plugin instance injection during execution."""

    def setUp(self):
        """Set up test fixtures."""
        self.adapter = HookAdapter()
        self.plugin = MockPlugin("test_plugin")
        self.callback = AsyncMock(return_value=None)
        self.adapter.register_plugin("test_plugin", self.plugin)

    def test_inject_plugin_instance(self):
        """Test injecting plugin instance for execution."""
        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=self.callback,
        )

        context = self.adapter.inject_plugin_instance_for_execution(hook)

        self.assertIn("hook", context)
        self.assertIn("plugin_instance", context)
        self.assertIs(context["hook"], hook)
        self.assertIs(context["plugin_instance"], self.plugin)

    def test_inject_for_unregistered_plugin_raises(self):
        """Test that injection fails for unregistered plugin."""
        hook = Hook(
            name="test_hook",
            plugin_name="nonexistent_plugin",
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=self.callback,
        )

        with self.assertRaises(ValueError) as context:
            self.adapter.inject_plugin_instance_for_execution(hook)

        self.assertIn("Plugin instance not found", str(context.exception))


class TestHookAdapterBackwardCompatibility(unittest.TestCase):
    """Test backward compatibility with both formats."""

    def setUp(self):
        """Set up test fixtures."""
        self.adapter = HookAdapter()
        self.plugin1 = MockPlugin("plugin1")
        self.plugin2 = MockPlugin("plugin2")
        self.callback = AsyncMock(return_value=None)

        self.adapter.register_plugin("plugin1", self.plugin1)
        self.adapter.register_plugin("plugin2", self.plugin2)

    def test_mixed_format_registration(self):
        """Test registering both old and new format hooks."""
        old_hook = OldFormatHook(
            name="old_hook",
            plugin=self.plugin1,
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=self.callback,
        )

        new_hook = Hook(
            name="new_hook",
            plugin_name="plugin2",
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=self.callback,
        )

        adapted_old = self.adapter.adapt_hook_for_registration(old_hook)
        adapted_new = self.adapter.adapt_hook_for_registration(new_hook)

        # Both should be in new format
        self.assertEqual(adapted_old.plugin_name, "plugin1")
        self.assertEqual(adapted_new.plugin_name, "plugin2")

    def test_multiple_conversions_same_plugin(self):
        """Test converting multiple hooks for same plugin."""
        old_hook1 = OldFormatHook(
            name="hook1",
            plugin=self.plugin1,
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=self.callback,
        )

        old_hook2 = OldFormatHook(
            name="hook2",
            plugin=self.plugin1,
            event_type=EventType.LLM_REQUEST,
            priority=100,
            callback=self.callback,
        )

        adapted1 = self.adapter.adapt_hook_for_registration(old_hook1)
        adapted2 = self.adapter.adapt_hook_for_registration(old_hook2)

        # Both should reference same plugin
        self.assertEqual(adapted1.plugin_name, "plugin1")
        self.assertEqual(adapted2.plugin_name, "plugin1")

    def test_plugin_identity_lookup_by_registration(self):
        """Test finding plugin by object identity when already registered."""

        # Create plugin without name attribute
        class NamelessPlugin:
            pass

        nameless_plugin = NamelessPlugin()

        # Register it with a name
        self.adapter.register_plugin("nameless", nameless_plugin)

        # Create old format hook with the nameless plugin
        old_hook = OldFormatHook(
            name="test_hook",
            plugin=nameless_plugin,
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=self.callback,
        )

        # Should find it by object identity
        adapted = self.adapter.adapt_hook_for_registration(old_hook)

        self.assertEqual(adapted.plugin_name, "nameless")


class TestHookAdapterStatistics(unittest.TestCase):
    """Test statistics tracking."""

    def setUp(self):
        """Set up test fixtures."""
        self.adapter = HookAdapter()
        self.plugin = MockPlugin("test_plugin")
        self.callback = AsyncMock(return_value=None)

    def test_conversion_rate_calculation(self):
        """Test conversion rate calculation."""
        self.adapter.register_plugin("test_plugin", self.plugin)

        # Register 3 old format hooks (all converted)
        for i in range(3):
            old_hook = OldFormatHook(
                name=f"hook_{i}",
                plugin=self.plugin,
                event_type=EventType.USER_INPUT,
                priority=100,
                callback=self.callback,
            )
            self.adapter.adapt_hook_for_registration(old_hook)

        # Register 2 new format hooks (not converted)
        for i in range(2):
            new_hook = Hook(
                name=f"new_hook_{i}",
                plugin_name="test_plugin",
                event_type=EventType.USER_INPUT,
                priority=100,
                callback=self.callback,
            )
            self.adapter.adapt_hook_for_registration(new_hook)

        stats = self.adapter.get_stats()
        self.assertEqual(stats["hooks_registered"], 5)
        self.assertEqual(stats["hooks_converted"], 3)
        self.assertAlmostEqual(stats["conversion_rate"], 3 / 5)

    def test_reset_stats(self):
        """Test resetting statistics."""
        self.adapter.register_plugin("test_plugin", self.plugin)

        old_hook = OldFormatHook(
            name="test_hook",
            plugin=self.plugin,
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=self.callback,
        )
        self.adapter.adapt_hook_for_registration(old_hook)

        self.adapter.reset_stats()

        stats = self.adapter.get_stats()
        self.assertEqual(stats["hooks_registered"], 0)
        self.assertEqual(stats["hooks_converted"], 0)
        # plugins_tracked should persist
        self.assertEqual(stats["plugins_tracked"], 1)

    def test_clear_removes_all_state(self):
        """Test that clear removes all state."""
        self.adapter.register_plugin("test_plugin", self.plugin)

        old_hook = OldFormatHook(
            name="test_hook",
            plugin=self.plugin,
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=self.callback,
        )
        self.adapter.adapt_hook_for_registration(old_hook)

        self.adapter.clear()

        self.assertIsNone(self.adapter.get_plugin_instance("test_plugin"))

        stats = self.adapter.get_stats()
        self.assertEqual(stats["plugins_tracked"], 0)
        self.assertEqual(stats["hooks_registered"], 0)


if __name__ == "__main__":
    unittest.main()
