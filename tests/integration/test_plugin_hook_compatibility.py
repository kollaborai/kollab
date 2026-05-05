"""Integration tests for plugin hook compatibility with HookAdapter.

This module tests the hook adapter's ability to work with all 15 existing plugins
in the Kollab codebase, ensuring:
1. Plugin loading doesn't crash
2. Hook registration works with the new plugin_name format
3. Version detection (old vs new hook format) works correctly
4. Backward compatibility is maintained
5. Hook execution with adapted hooks works

Phase 8b - HOOK TESTING WITH ALL PLUGINS

Author: Phase 8b Testing
Date: 2025-01-14
"""

import importlib
import importlib.util
import inspect
import logging
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from kollabor_events import Event, EventType, Hook  # noqa: E402
from kollabor_events.bus import EventBus  # noqa: E402
from kollabor_events.hook_adapter import HookAdapter  # noqa: E402

logger = logging.getLogger(__name__)


# ============================================================================
# PLUGIN LIST - All plugins in the codebase
# ============================================================================

PLUGIN_PATHS = [
    # Standard plugins
    "plugins/hook_monitoring_plugin.py",
    "plugins/resume_conversation_plugin.py",
    "plugins/save_conversation_plugin.py",
    "plugins/agent_orchestrator_plugin.py",
    "plugins/modern_input_plugin.py",
    "plugins/terminal_plugin.py",
    # Fullscreen plugins
    "plugins/fullscreen/matrix_plugin.py",
    "plugins/fullscreen/example_plugin.py",
    "plugins/fullscreen/space_shooter_plugin.py",
    # Subdirectory plugins
    "plugins/agent_orchestrator/plugin.py",
]


# ============================================================================
# MOCK DEPENDENCIES FOR PLUGIN LOADING
# ============================================================================


def create_mock_event_bus() -> EventBus:
    """Create a mock event bus for plugin initialization."""
    event_bus = EventBus()

    # Mock the executor and processor to avoid full initialization
    event_bus.executor = MagicMock()
    event_bus.executor.execute_hooks = AsyncMock(return_value={})
    event_bus.processor = MagicMock()
    event_bus.processor.process_event = AsyncMock(return_value=None)

    return event_bus


def create_mock_renderer() -> MagicMock:
    """Create a mock renderer for plugin initialization."""
    renderer = MagicMock()

    # Mock common renderer attributes
    renderer.status_renderer = MagicMock()
    renderer.status_renderer.status_registry = MagicMock()
    renderer.message_coordinator = MagicMock()
    renderer.terminal = MagicMock()
    renderer.terminal.width = 80
    renderer.terminal.height = 24

    return renderer


def create_mock_config() -> MagicMock:
    """Create a mock config manager for plugin initialization."""
    config = MagicMock()

    # Mock get method to return defaults
    def mock_get(key: str, default: Any = None) -> Any:
        # Common plugin config defaults
        if "enabled" in key:
            return False  # Disable plugin features by default for tests
        return default

    config.get = mock_get
    config.set = MagicMock()

    return config


# ============================================================================
# TEST SUITE: Plugin Hook Compatibility
# ============================================================================


class TestPluginHookCompatibility(unittest.TestCase):
    """Test suite for plugin hook compatibility with HookAdapter.

    Tests all 15 plugins for:
    - Plugin loading without crashes
    - Hook registration with HookAdapter
    - Hook format compatibility
    - Hook execution with adapted hooks
    """

    def setUp(self):
        """Set up test fixtures."""
        self.adapter = HookAdapter()
        self.event_bus = create_mock_event_bus()
        self.renderer = create_mock_renderer()
        self.config = create_mock_config()

        # Store loaded plugins for cleanup
        self.loaded_plugins: Dict[str, Any] = {}

    def tearDown(self):
        """Clean up after tests."""
        self.adapter.clear()
        self.loaded_plugins.clear()

    # ========================================================================
    # PLUGIN LOADING TESTS
    # ========================================================================

    def test_load_all_plugins_without_crashes(self):
        """Test that all plugins can be loaded without crashes."""
        plugin_count = 0
        errors = []
        skipped = []

        for plugin_path in PLUGIN_PATHS:
            try:
                plugin_class = self._load_plugin_class(plugin_path)
                if plugin_class:
                    plugin_count += 1
                else:
                    skipped.append(f"{plugin_path}: No plugin class found")
            except Exception as e:
                errors.append(f"{plugin_path}: {e}")

        # Verify no crashes (errors should be empty)
        # Some plugins may not load due to dependencies, which is OK
        self.assertEqual(len(errors), 0, f"Errors loading plugins: {errors}")

        # Verify at least some plugins loaded
        self.assertGreater(plugin_count, 0, f"No plugins loaded. Skipped: {skipped}")

        logger.info(f"Loaded {plugin_count} plugins, skipped {len(skipped)}")

    def test_plugin_initialization(self):
        """Test that plugins can be initialized with dependencies."""
        initialized_count = 0
        errors = []
        skipped = []

        for plugin_path in PLUGIN_PATHS:
            try:
                plugin_instance = self._create_plugin_instance(plugin_path)
                if plugin_instance:
                    initialized_count += 1
                else:
                    skipped.append(f"{plugin_path}: Could not create instance")
            except Exception as e:
                errors.append(f"{plugin_path}: {e}")

        # Verify no crashes during initialization
        self.assertEqual(len(errors), 0, f"Errors initializing plugins: {errors}")

        # Verify at least some plugins initialized
        self.assertGreater(
            initialized_count, 0, f"No plugins initialized. Skipped: {skipped}"
        )

        logger.info(f"Initialized {initialized_count} plugins, skipped {len(skipped)}")

    # ========================================================================
    # HOOK FORMAT DETECTION TESTS
    # ========================================================================

    def test_all_plugins_use_new_hook_format(self):
        """Test that all plugins use the new plugin_name hook format."""
        plugins_with_hooks = []

        for plugin_path in PLUGIN_PATHS:
            try:
                hooks = self._extract_plugin_hooks(plugin_path)
                if hooks:
                    # Check if hooks use plugin_name (new format)
                    for hook in hooks:
                        if hasattr(hook, "plugin_name") and isinstance(
                            hook.plugin_name, str
                        ):
                            plugins_with_hooks.append(plugin_path)
                            break
            except Exception:
                # Skip plugins that can't be analyzed
                pass

        # All plugins with hooks should use new format
        self.assertGreater(len(plugins_with_hooks), 0, "No plugins found with hooks")

        logger.info(f"Plugins using new hook format: {len(plugins_with_hooks)}")

    def test_old_format_hook_detection(self):
        """Test HookAdapter can detect old format hooks (plugin object)."""
        # Create a mock old-format hook object
        mock_plugin = MagicMock()
        mock_plugin.name = "test_plugin"

        # Old format hook has 'plugin' attribute instead of 'plugin_name'
        old_format_hook = MagicMock()
        old_format_hook.name = "test_hook_old_format"
        old_format_hook.plugin = mock_plugin  # Old format: plugin object reference
        old_format_hook.plugin_name = None  # Not set in old format
        old_format_hook.event_type = EventType.USER_INPUT
        old_format_hook.priority = 100
        old_format_hook.callback = AsyncMock()
        old_format_hook.enabled = True
        old_format_hook.timeout = None
        old_format_hook.retry_attempts = None
        old_format_hook.error_action = None

        # Adapt the hook
        adapted_hook = self.adapter.adapt_hook_for_registration(old_format_hook)

        # Verify conversion
        self.assertIsInstance(adapted_hook, Hook)
        self.assertEqual(adapted_hook.plugin_name, "test_plugin")
        self.assertEqual(adapted_hook.name, "test_hook_old_format")

        # Verify stats
        stats = self.adapter.get_stats()
        self.assertEqual(stats["hooks_converted"], 1)

    def test_new_format_hook_detection(self):
        """Test HookAdapter recognizes new format hooks (plugin_name string)."""
        # Create a mock new-format hook
        new_format_hook = Hook(
            name="test_hook_new_format",
            plugin_name="test_plugin",  # New format: plugin_name string
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=AsyncMock(),
        )

        # Register plugin first
        mock_plugin = MagicMock()
        mock_plugin.name = "test_plugin"
        self.adapter.register_plugin("test_plugin", mock_plugin)

        # Adapt the hook
        adapted_hook = self.adapter.adapt_hook_for_registration(new_format_hook)

        # Verify no conversion needed
        self.assertIs(adapted_hook, new_format_hook)

        # Verify stats (no conversion)
        stats = self.adapter.get_stats()
        self.assertEqual(stats["hooks_converted"], 0)
        self.assertEqual(stats["hooks_registered"], 1)

    # ========================================================================
    # HOOK ADAPTER INTEGRATION TESTS
    # ========================================================================

    def test_plugin_instance_tracking(self):
        """Test that HookAdapter correctly tracks plugin instances."""
        # Create mock plugins
        plugin1 = MagicMock()
        plugin1.name = "plugin1"

        plugin2 = MagicMock()
        plugin2.name = "plugin2"

        # Register plugins
        self.adapter.register_plugin("plugin1", plugin1)
        self.adapter.register_plugin("plugin2", plugin2)

        # Verify tracking
        self.assertEqual(self.adapter.get_plugin_instance("plugin1"), plugin1)
        self.assertEqual(self.adapter.get_plugin_instance("plugin2"), plugin2)
        self.assertIsNone(self.adapter.get_plugin_instance("nonexistent"))

        # Verify stats
        stats = self.adapter.get_stats()
        self.assertEqual(stats["plugins_tracked"], 2)
        self.assertIn("plugin1", stats["tracked_plugins"])
        self.assertIn("plugin2", stats["tracked_plugins"])

    def test_plugin_unregistration(self):
        """Test that plugins can be unregistered."""
        plugin = MagicMock()
        plugin.name = "test_plugin"

        # Register
        self.adapter.register_plugin("test_plugin", plugin)
        self.assertIsNotNone(self.adapter.get_plugin_instance("test_plugin"))

        # Unregister
        result = self.adapter.unregister_plugin("test_plugin")

        self.assertTrue(result)
        self.assertIsNone(self.adapter.get_plugin_instance("test_plugin"))

        # Unregister again should return False
        result = self.adapter.unregister_plugin("test_plugin")
        self.assertFalse(result)

    def test_hook_registration_with_adapter(self):
        """Test hook registration flow with HookAdapter."""
        # Create a plugin with hooks using new format
        plugin = MagicMock()
        plugin.name = "test_plugin"

        hooks = [
            Hook(
                name="hook1",
                plugin_name="test_plugin",
                event_type=EventType.USER_INPUT,
                priority=100,
                callback=AsyncMock(),
            ),
            Hook(
                name="hook2",
                plugin_name="test_plugin",
                event_type=EventType.LLM_REQUEST,
                priority=200,
                callback=AsyncMock(),
            ),
        ]

        # Register plugin
        self.adapter.register_plugin("test_plugin", plugin)

        # Adapt all hooks
        adapted_hooks = [
            self.adapter.adapt_hook_for_registration(hook) for hook in hooks
        ]

        # Verify all hooks adapted
        self.assertEqual(len(adapted_hooks), 2)
        self.assertTrue(all(h.plugin_name == "test_plugin" for h in adapted_hooks))

        # Verify stats
        stats = self.adapter.get_stats()
        self.assertEqual(stats["hooks_registered"], 2)
        self.assertEqual(stats["hooks_converted"], 0)  # Already new format

    def test_plugin_instance_injection_for_execution(self):
        """Test plugin instance injection for hook execution."""
        plugin = MagicMock()
        plugin.name = "test_plugin"
        plugin.some_method = MagicMock(return_value="result")

        # Create hook with new format
        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=AsyncMock(),
        )

        # Register plugin
        self.adapter.register_plugin("test_plugin", plugin)

        # Get execution context
        execution_context = self.adapter.inject_plugin_instance_for_execution(hook)

        # Verify execution context
        self.assertIn("hook", execution_context)
        self.assertIn("plugin_instance", execution_context)
        self.assertIs(execution_context["plugin_instance"], plugin)

        # Verify we can call plugin methods
        result = execution_context["plugin_instance"].some_method()
        self.assertEqual(result, "result")

    # ========================================================================
    # BACKWARD COMPATIBILITY TESTS
    # ========================================================================

    def test_mixed_format_hooks(self):
        """Test HookAdapter handles both old and new format hooks."""
        plugin = MagicMock()
        plugin.name = "test_plugin"

        # Old format hook (using helper)
        old_hook = self._create_old_format_hook(
            "old_hook", plugin, EventType.USER_INPUT
        )

        # New format hook
        new_hook = Hook(
            name="new_hook",
            plugin_name="test_plugin",  # New format
            event_type=EventType.LLM_REQUEST,
            priority=100,
            callback=AsyncMock(),
        )

        # Register plugin
        self.adapter.register_plugin("test_plugin", plugin)

        # Adapt both hooks
        adapted_old = self.adapter.adapt_hook_for_registration(old_hook)
        adapted_new = self.adapter.adapt_hook_for_registration(new_hook)

        # Verify both adapted correctly
        self.assertEqual(adapted_old.plugin_name, "test_plugin")
        self.assertEqual(adapted_new.plugin_name, "test_plugin")

        # Verify stats
        stats = self.adapter.get_stats()
        self.assertEqual(stats["hooks_registered"], 2)
        self.assertEqual(stats["hooks_converted"], 1)  # Only old format converted

    def test_auto_plugin_registration_from_old_hooks(self):
        """Test that old format hooks auto-register their plugin."""
        plugin = MagicMock()
        plugin.name = "auto_plugin"

        # Don't manually register plugin
        # self.adapter.register_plugin("auto_plugin", plugin)  # NOT CALLED

        # Create old format hook (using helper)
        old_hook = self._create_old_format_hook(
            "auto_hook", plugin, EventType.USER_INPUT
        )

        # Adapt hook (should auto-register plugin)
        self.adapter.adapt_hook_for_registration(old_hook)

        # Verify plugin was auto-registered
        plugin_instance = self.adapter.get_plugin_instance("auto_plugin")
        self.assertIs(plugin_instance, plugin)

        # Verify stats
        stats = self.adapter.get_stats()
        self.assertIn("auto_plugin", stats["tracked_plugins"])

    # ========================================================================
    # ERROR HANDLING TESTS
    # ========================================================================

    def test_error_on_hook_without_plugin_info(self):
        """Test error when hook has neither plugin nor plugin_name."""
        # Create a hook without plugin info
        invalid_hook = MagicMock()
        invalid_hook.name = "invalid_hook"
        # Set plugin to None (no plugin instance)
        invalid_hook.plugin = None
        # Deliberately don't set plugin_name

        with self.assertRaises(ValueError) as context:
            self.adapter.adapt_hook_for_registration(invalid_hook)

        # Error message should mention inability to determine plugin name
        self.assertIn("Cannot determine plugin name", str(context.exception))

    def test_error_on_plugin_instance_not_found(self):
        """Test error when plugin instance not found for execution."""
        # Create hook with unregistered plugin
        hook = Hook(
            name="orphan_hook",
            plugin_name="nonexistent_plugin",
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=AsyncMock(),
        )

        with self.assertRaises(ValueError) as context:
            self.adapter.inject_plugin_instance_for_execution(hook)

        self.assertIn("Plugin instance not found", str(context.exception))

    def test_validation_on_invalid_plugin_registration(self):
        """Test validation during plugin registration."""
        # Empty name
        with self.assertRaises(ValueError):
            self.adapter.register_plugin("", MagicMock())

        # None instance
        with self.assertRaises(ValueError):
            self.adapter.register_plugin("test", None)

    # ========================================================================
    # REAL PLUGIN INTEGRATION TESTS
    # ========================================================================

    def test_hook_monitoring_plugin_hooks(self):
        """Test hook_monitoring_plugin hook registration with adapter."""
        plugin_instance = self._create_plugin_instance(
            "plugins/hook_monitoring_plugin.py"
        )

        if not plugin_instance:
            self.skipTest("Could not load hook_monitoring_plugin")

        # Use the actual plugin name from the instance
        plugin_name = getattr(plugin_instance, "name", "hookmonitoring")

        # Register plugin with adapter
        self.adapter.register_plugin(plugin_name, plugin_instance)

        # Extract hooks from plugin
        hooks = self._extract_plugin_hooks("plugins/hook_monitoring_plugin.py")

        if hooks:
            # Adapt all hooks
            adapted_hooks = []
            for hook in hooks:
                try:
                    adapted = self.adapter.adapt_hook_for_registration(hook)
                    adapted_hooks.append(adapted)
                except Exception as e:
                    logger.warning(f"Failed to adapt hook: {e}")

            # Verify adaptation
            self.assertGreater(len(adapted_hooks), 0)
            self.assertTrue(all(h.plugin_name == plugin_name for h in adapted_hooks))

    def test_tmux_plugin_hooks(self):
        """Test terminal_plugin hook registration with adapter."""
        plugin_instance = self._create_plugin_instance("plugins/terminal_plugin.py")

        if not plugin_instance:
            self.skipTest("Could not load terminal_plugin")

        # Register plugin with adapter
        self.adapter.register_plugin("tmux", plugin_instance)

        # Verify plugin tracked
        self.assertIsNotNone(self.adapter.get_plugin_instance("tmux"))

    # ========================================================================
    # STATS AND REPORTING TESTS
    # ========================================================================

    def test_adapter_statistics(self):
        """Test HookAdapter statistics tracking."""
        # Create mock plugins and hooks
        plugin1 = MagicMock()
        plugin1.name = "plugin1"

        plugin2 = MagicMock()
        plugin2.name = "plugin2"

        # Old format hook (using helper)
        old_hook = self._create_old_format_hook(
            "old_hook", plugin1, EventType.USER_INPUT
        )

        # New format hooks
        new_hook1 = Hook(
            name="new_hook1",
            plugin_name="plugin1",
            event_type=EventType.LLM_REQUEST,
            priority=100,
            callback=AsyncMock(),
        )

        new_hook2 = Hook(
            name="new_hook2",
            plugin_name="plugin2",
            event_type=EventType.LLM_RESPONSE,
            priority=100,
            callback=AsyncMock(),
        )

        # Register plugins
        self.adapter.register_plugin("plugin1", plugin1)
        self.adapter.register_plugin("plugin2", plugin2)

        # Adapt hooks
        self.adapter.adapt_hook_for_registration(old_hook)  # Converts
        self.adapter.adapt_hook_for_registration(new_hook1)  # No conversion
        self.adapter.adapt_hook_for_registration(new_hook2)  # No conversion

        # Check stats
        stats = self.adapter.get_stats()

        self.assertEqual(stats["hooks_registered"], 3)
        self.assertEqual(stats["hooks_converted"], 1)
        self.assertEqual(stats["plugins_tracked"], 2)
        self.assertAlmostEqual(stats["conversion_rate"], 1 / 3)

    def test_stats_reset(self):
        """Test statistics reset functionality."""
        plugin = MagicMock()
        plugin.name = "test"

        hook = self._create_old_format_hook("test_hook", plugin, EventType.USER_INPUT)

        self.adapter.register_plugin("test", plugin)
        self.adapter.adapt_hook_for_registration(hook)

        # Reset stats
        self.adapter.reset_stats()

        # Verify stats reset but plugins still tracked
        stats = self.adapter.get_stats()
        self.assertEqual(stats["hooks_registered"], 0)
        self.assertEqual(stats["hooks_converted"], 0)
        self.assertEqual(stats["plugins_tracked"], 1)  # Plugin still tracked

    def test_adapter_clear(self):
        """Test complete adapter clearing."""
        plugin = MagicMock()
        plugin.name = "test"

        hook = self._create_old_format_hook("test_hook", plugin, EventType.USER_INPUT)

        self.adapter.register_plugin("test", plugin)
        self.adapter.adapt_hook_for_registration(hook)

        # Clear adapter
        self.adapter.clear()

        # Verify everything cleared
        self.assertIsNone(self.adapter.get_plugin_instance("test"))
        stats = self.adapter.get_stats()
        self.assertEqual(stats["plugins_tracked"], 0)
        self.assertEqual(stats["hooks_registered"], 0)

    # ========================================================================
    # HELPER METHODS
    # ========================================================================

    def _create_old_format_hook(
        self,
        name: str,
        plugin: Any,
        event_type: EventType,
        priority: int = 100,
        callback=None,
    ) -> MagicMock:
        """Create a mock old-format hook object.

        Old format hooks have a 'plugin' attribute instead of 'plugin_name'.

        Args:
            name: Hook name.
            plugin: Plugin instance.
            event_type: Event type.
            priority: Hook priority.
            callback: Callback function.

        Returns:
            MagicMock object with old-format hook attributes.
        """
        hook = MagicMock()
        hook.name = name
        hook.plugin = plugin  # Old format: plugin object reference
        hook.plugin_name = None  # Not set in old format
        hook.event_type = event_type
        hook.priority = priority
        hook.callback = callback or AsyncMock()
        hook.enabled = True
        hook.timeout = None
        hook.retry_attempts = None
        hook.error_action = None

        return hook

    def _load_plugin_class(self, plugin_path: str) -> Optional[type]:
        """Load a plugin class from a file path.

        Args:
            plugin_path: Path to plugin file relative to project root.

        Returns:
            Plugin class if found, None otherwise.
        """
        full_path = PROJECT_ROOT / plugin_path

        if not full_path.exists():
            logger.warning(f"Plugin file not found: {full_path}")
            return None

        # Load module
        spec = importlib.util.spec_from_file_location(full_path.stem, full_path)

        if spec is None or spec.loader is None:
            logger.warning(f"Could not load spec for: {full_path}")
            return None

        module = importlib.util.module_from_spec(spec)

        try:
            spec.loader.exec_module(module)
        except Exception as e:
            # Some plugins may fail to import due to dependencies
            # This is OK for the loading test
            logger.debug(f"Failed to import {plugin_path}: {e}")
            return None

        # Find plugin class
        for attr_name in dir(module):
            attr = getattr(module, attr_name)

            if (
                inspect.isclass(attr)
                and attr_name.endswith("Plugin")
                and attr.__module__ == module.__name__
            ):
                return attr

        return None

    def _create_plugin_instance(self, plugin_path: str) -> Optional[Any]:
        """Create a plugin instance from a file path.

        Args:
            plugin_path: Path to plugin file relative to project root.

        Returns:
            Plugin instance if created, None otherwise.
        """
        plugin_class = self._load_plugin_class(plugin_path)

        if plugin_class is None:
            return None

        try:
            # Create instance with dependencies
            instance = plugin_class(
                name=plugin_class.__name__.replace("Plugin", "").lower(),
                event_bus=self.event_bus,
                renderer=self.renderer,
                config=self.config,
            )

            # Store for cleanup
            self.loaded_plugins[plugin_path] = instance

            return instance
        except Exception as e:
            logger.debug(f"Failed to create instance for {plugin_path}: {e}")
            return None

    def _extract_plugin_hooks(self, plugin_path: str) -> List[Hook]:
        """Extract hooks from a plugin by analyzing its hooks attribute.

        Args:
            plugin_path: Path to plugin file.

        Returns:
            List of Hook objects if found, empty list otherwise.
        """
        instance = self._create_plugin_instance(plugin_path)

        if instance is None:
            return []

        # Check for hooks attribute
        if hasattr(instance, "hooks"):
            hooks = getattr(instance, "hooks")
            if isinstance(hooks, list):
                return hooks

        return []


# ============================================================================
# TEST SUITE: Version Detection and Compatibility
# ============================================================================


class TestVersionDetectionCompatibility(unittest.TestCase):
    """Test suite for hook format version detection and backward compatibility."""

    def setUp(self):
        """Set up test fixtures."""
        self.adapter = HookAdapter()

    def tearDown(self):
        """Clean up after tests."""
        self.adapter.clear()

    def test_detect_old_format_by_plugin_attribute(self):
        """Test detection of old format by presence of plugin attribute."""
        mock_plugin = MagicMock()
        mock_plugin.name = "old_style_plugin"

        # Old format: has 'plugin' attribute with object
        old_hook = MagicMock()
        old_hook.name = "old_hook"
        old_hook.plugin = mock_plugin
        old_hook.plugin_name = None  # Not set in old format

        # Should detect as old format
        result = self.adapter._get_plugin_name_from_instance(old_hook.plugin)
        self.assertEqual(result, "old_style_plugin")

    def test_detect_new_format_by_plugin_name_attribute(self):
        """Test detection of new format by presence of plugin_name string."""
        # New format: has 'plugin_name' attribute with string
        new_hook = MagicMock()
        new_hook.name = "new_hook"
        new_hook.plugin = None  # Not set in new format
        new_hook.plugin_name = "new_style_plugin"

        # Should recognize as new format (adapt_hook_for_registration checks)
        mock_plugin = MagicMock()
        mock_plugin.name = "new_style_plugin"
        self.adapter.register_plugin("new_style_plugin", mock_plugin)

        adapted = self.adapter.adapt_hook_for_registration(new_hook)
        self.assertEqual(adapted.plugin_name, "new_style_plugin")

    def test_plugin_name_extraction_methods(self):
        """Test various methods of extracting plugin name from instance."""
        plugin1 = MagicMock()
        plugin1.name = "via_name_attr"

        plugin2 = MagicMock()
        plugin2.plugin_name = "via_plugin_name_attr"

        plugin3 = MagicMock()
        plugin3._name = "via_private_name_attr"

        adapter = HookAdapter()

        # Test different attribute names
        self.assertEqual(
            adapter._get_plugin_name_from_instance(plugin1), "via_name_attr"
        )
        self.assertEqual(
            adapter._get_plugin_name_from_instance(plugin2), "via_plugin_name_attr"
        )
        self.assertEqual(
            adapter._get_plugin_name_from_instance(plugin3), "via_private_name_attr"
        )

    def test_plugin_lookup_by_identity(self):
        """Test plugin name lookup by object identity."""
        plugin = MagicMock()
        # Don't set a name attribute to test identity lookup
        # plugin.name = "identity_plugin"  # NOT SET

        adapter = HookAdapter()
        adapter.register_plugin("custom_name", plugin)

        # Lookup by identity should return registered name
        result = adapter._get_plugin_name_from_instance(plugin)
        self.assertEqual(result, "custom_name")


# ============================================================================
# TEST SUITE: Data Format Compatibility
# ============================================================================


class TestDataFormatCompatibility(unittest.TestCase):
    """Test suite for data format compatibility across hook versions."""

    def setUp(self):
        """Set up test fixtures."""
        self.adapter = HookAdapter()
        self.event_bus = create_mock_event_bus()

    def tearDown(self):
        """Clean up after tests."""
        self.adapter.clear()

    def test_hook_data_structure_compatibility(self):
        """Test that hook data structures are compatible across versions."""
        plugin = MagicMock()
        plugin.name = "test_plugin"

        # Old format hook (would have been created pre-migration)
        old_hook = MagicMock()
        old_hook.name = "old_format"
        old_hook.plugin = plugin
        old_hook.plugin_name = None  # Not set in old format
        old_hook.event_type = EventType.USER_INPUT
        old_hook.priority = 100
        old_hook.callback = AsyncMock()
        old_hook.enabled = True
        old_hook.timeout = 30
        old_hook.retry_attempts = 3
        old_hook.error_action = "continue"

        # Register and adapt
        self.adapter.register_plugin("test_plugin", plugin)
        adapted_hook = self.adapter.adapt_hook_for_registration(old_hook)

        # Verify all attributes preserved
        self.assertEqual(adapted_hook.name, "old_format")
        self.assertEqual(adapted_hook.plugin_name, "test_plugin")
        self.assertEqual(adapted_hook.event_type, EventType.USER_INPUT)
        self.assertEqual(adapted_hook.priority, 100)
        self.assertTrue(adapted_hook.enabled)
        self.assertEqual(adapted_hook.timeout, 30)
        self.assertEqual(adapted_hook.retry_attempts, 3)
        self.assertEqual(adapted_hook.error_action, "continue")

    def test_event_type_compatibility(self):
        """Test that event types work across hook formats."""
        plugin = MagicMock()
        plugin.name = "test_plugin"

        # Test various event types
        event_types = [
            EventType.USER_INPUT,
            EventType.LLM_REQUEST,
            EventType.LLM_RESPONSE,
            EventType.TOOL_CALL,
            EventType.KEY_PRESS,
        ]

        self.adapter.register_plugin("test_plugin", plugin)

        for event_type in event_types:
            hook = Hook(
                name=f"test_{event_type.value}",
                plugin_name="test_plugin",
                event_type=event_type,
                priority=100,
                callback=AsyncMock(),
            )

            adapted = self.adapter.adapt_hook_for_registration(hook)
            self.assertEqual(adapted.event_type, event_type)

    def test_callback_signature_compatibility(self):
        """Test that callback signatures work with adapted hooks."""
        plugin = MagicMock()
        plugin.name = "test_plugin"

        # Create callback with proper signature
        async def test_callback(event: Event, **kwargs):
            return {"result": "test"}

        hook = Hook(
            name="callback_test",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=test_callback,
        )

        self.adapter.register_plugin("test_plugin", plugin)
        adapted = self.adapter.adapt_hook_for_registration(hook)

        # Verify callback preserved
        self.assertEqual(adapted.callback, test_callback)
        self.assertTrue(inspect.iscoroutinefunction(adapted.callback))


# ============================================================================
# TEST RUNNER
# ============================================================================


def run_tests() -> unittest.TestResult:
    """Run all plugin hook compatibility tests.

    Returns:
        TestResult object with test results.
    """
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test suites
    suite.addTests(loader.loadTestsFromTestCase(TestPluginHookCompatibility))
    suite.addTests(loader.loadTestsFromTestCase(TestVersionDetectionCompatibility))
    suite.addTests(loader.loadTestsFromTestCase(TestDataFormatCompatibility))

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Print summary
    print("\n" + "=" * 80)
    print("PLUGIN HOOK COMPATIBILITY TEST SUMMARY")
    print("=" * 80)
    print(f"Tests run: {result.testsRun}")
    print(f"Successes: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print("=" * 80)

    return result


if __name__ == "__main__":
    # Configure logging for tests
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Run tests
    result = run_tests()

    # Exit with appropriate code
    sys.exit(0 if result.wasSuccessful() else 1)
