"""Tests for EventBus and related components."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.append(str(Path(__file__).parent.parent.parent))

from kollabor_events import EventBus, EventType, Hook, HookPriority
from kollabor_events.executor import HookExecutor
from kollabor_events.processor import EventProcessor
from kollabor_events.registry import HookRegistry


class TestEventBus(unittest.TestCase):
    """Test cases for EventBus."""

    def setUp(self):
        """Set up test fixtures."""
        self.event_bus = EventBus()

    def test_event_bus_initialization(self):
        """Test that EventBus initializes with all components."""
        self.assertIsInstance(self.event_bus.hook_registry, HookRegistry)
        self.assertIsInstance(self.event_bus.hook_executor, HookExecutor)
        self.assertIsInstance(self.event_bus.event_processor, EventProcessor)

    def test_hook_registration(self):
        """Test hook registration."""
        # Create a mock hook
        mock_callback = AsyncMock(return_value={"status": "success"})
        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.LLM.value,
            callback=mock_callback,
        )

        # Test async registration
        async def test_registration():
            result = await self.event_bus.register_hook(hook)
            self.assertTrue(result)

            # Check that hook is registered
            hook_count = self.event_bus.get_hooks_for_event(EventType.USER_INPUT)
            self.assertEqual(hook_count, 1)

        asyncio.run(test_registration())

    def test_hook_unregistration(self):
        """Test hook unregistration."""
        # Create and register a hook
        mock_callback = AsyncMock(return_value={"status": "success"})
        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.LLM.value,
            callback=mock_callback,
        )

        async def test_unregistration():
            # Register hook
            await self.event_bus.register_hook(hook)
            self.assertEqual(
                self.event_bus.get_hooks_for_event(EventType.USER_INPUT), 1
            )

            # Unregister hook
            result = await self.event_bus.unregister_hook("test_plugin", "test_hook")
            self.assertTrue(result)

            # Check that hook is unregistered
            hook_count = self.event_bus.get_hooks_for_event(EventType.USER_INPUT)
            self.assertEqual(hook_count, 0)

        asyncio.run(test_unregistration())

    def test_event_processing(self):
        """Test event processing through the bus."""
        # Create a mock hook
        mock_callback = AsyncMock(return_value={"status": "processed"})
        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.LLM.value,
            callback=mock_callback,
        )

        async def test_processing():
            # Register hook
            await self.event_bus.register_hook(hook)

            # Process event
            test_data = {"input": "test message"}
            result = await self.event_bus.emit_with_hooks(
                EventType.USER_INPUT, test_data, "test_source"
            )

            # Check that event was processed
            self.assertIn("main", result)
            self.assertIsInstance(result["main"], dict)

            # Check that hook was called
            mock_callback.assert_called_once()

        asyncio.run(test_processing())

    def test_hook_enable_disable(self):
        """Test enabling and disabling hooks."""
        # Create a hook
        mock_callback = AsyncMock(return_value={"status": "success"})
        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.LLM.value,
            callback=mock_callback,
        )

        async def test_enable_disable():
            # Register hook
            await self.event_bus.register_hook(hook)

            # Disable hook
            result = self.event_bus.disable_hook("test_plugin", "test_hook")
            self.assertTrue(result)

            # Enable hook
            result = self.event_bus.enable_hook("test_plugin", "test_hook")
            self.assertTrue(result)

        asyncio.run(test_enable_disable())

    def test_hook_status_tracking(self):
        """Test hook status tracking."""
        # Create a hook
        mock_callback = AsyncMock(return_value={"status": "success"})
        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.LLM.value,
            callback=mock_callback,
        )

        async def test_status():
            # Register hook
            await self.event_bus.register_hook(hook)

            # Get hook status
            status = self.event_bus.get_hook_status()
            self.assertIn("test_plugin.test_hook", status["hook_details"])
            self.assertEqual(status["total_hooks"], 1)

            # Get registry stats
            stats = self.event_bus.get_registry_stats()
            self.assertEqual(stats["total_hooks"], 1)
            self.assertIn("hooks_per_plugin", stats)

        asyncio.run(test_status())


class TestHookRegistry(unittest.TestCase):
    """Test cases for HookRegistry."""

    def setUp(self):
        """Set up test fixtures."""
        self.registry = HookRegistry()

    def test_hook_registration_priority_sorting(self):
        """Test that hooks are sorted by priority."""
        # Create hooks with different priorities
        hook1 = Hook(
            name="low_priority",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=10,
            callback=AsyncMock(),
        )

        hook2 = Hook(
            name="high_priority",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=AsyncMock(),
        )

        # Register hooks in reverse priority order
        self.registry.register_hook(hook1)
        self.registry.register_hook(hook2)

        # Get hooks and verify sorting
        hooks = self.registry.get_hooks_for_event(EventType.USER_INPUT)
        self.assertEqual(len(hooks), 2)
        self.assertEqual(hooks[0].name, "high_priority")
        self.assertEqual(hooks[1].name, "low_priority")

    def test_duplicate_hook_registration(self):
        """Test handling of duplicate hook registration."""
        # Create a hook
        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=AsyncMock(),
        )

        # Register hook twice
        result1 = self.registry.register_hook(hook)
        result2 = self.registry.register_hook(hook)

        self.assertTrue(result1)
        self.assertTrue(result2)

        # Should still only have one hook
        hooks = self.registry.get_hooks_for_event(EventType.USER_INPUT)
        self.assertEqual(len(hooks), 1)


class TestHookExecutor(unittest.TestCase):
    """Test cases for HookExecutor."""

    def setUp(self):
        """Set up test fixtures."""
        self.executor = HookExecutor()

    def test_successful_hook_execution(self):
        """Test successful hook execution."""
        # Create a mock hook
        mock_callback = AsyncMock(return_value={"status": "success"})
        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=mock_callback,
        )

        # Create a mock event
        from kollabor_events.models import Event

        event = Event(type=EventType.USER_INPUT, data={"input": "test"}, source="test")

        async def test_execution():
            result = await self.executor.execute_hook(hook, event)

            self.assertTrue(result["success"])
            self.assertIsNone(result["error"])
            self.assertEqual(result["result"], {"status": "success"})
            self.assertGreater(result["duration_ms"], 0)

        asyncio.run(test_execution())

    def test_hook_timeout(self):
        """Test hook timeout handling."""

        # Create a slow mock hook
        async def slow_callback(data, event):
            await asyncio.sleep(2)  # Sleep longer than timeout
            return {"status": "success"}

        hook = Hook(
            name="slow_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=slow_callback,
            timeout=0.1,  # Very short timeout
            retry_attempts=0,  # No retries (avoids exponential backoff delays)
        )

        # Create a mock event
        from kollabor_events.models import Event

        event = Event(type=EventType.USER_INPUT, data={"input": "test"}, source="test")

        async def test_timeout():
            result = await self.executor.execute_hook(hook, event)

            self.assertFalse(result["success"])
            self.assertEqual(result["error"], "timeout")
            self.assertGreater(result["duration_ms"], 0)

        asyncio.run(test_timeout())

    def test_hook_exception(self):
        """Test hook exception handling."""

        # Create a failing mock hook
        async def failing_callback(data, event):
            raise ValueError("Test error")

        hook = Hook(
            name="failing_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=100,
            callback=failing_callback,
            retry_attempts=0,  # No retries (avoids exponential backoff delays)
        )

        # Create a mock event
        from kollabor_events.models import Event

        event = Event(type=EventType.USER_INPUT, data={"input": "test"}, source="test")

        async def test_exception():
            result = await self.executor.execute_hook(hook, event)

            self.assertFalse(result["success"])
            self.assertEqual(result["error"], "Test error")
            self.assertGreater(result["duration_ms"], 0)

        asyncio.run(test_exception())


if __name__ == "__main__":
    unittest.main()
