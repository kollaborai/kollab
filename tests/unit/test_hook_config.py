"""Unit tests for hook configuration and retry logic."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from kollabor_events.bus import EventBus
from kollabor_events.models import Event, EventType, Hook, HookPriority, HookStatus

_instant_sleep = AsyncMock(return_value=None)


class TestHookConfiguration(unittest.TestCase):
    """Test hook configuration defaults and retry logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = {
            "hooks": {
                "default_timeout": 60,
                "default_retries": 5,
                "default_error_action": "stop",
            }
        }
        self.event_bus = EventBus(config=self.config)

    def test_config_defaults_applied_during_execution(self):
        """Test that config defaults are applied during hook execution."""

        # Create a hook with default values (None = use config defaults)
        async def quick_callback(data, event):
            return {"status": "success"}

        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.PREPROCESSING.value,
            callback=quick_callback,
        )

        event = Event(type=EventType.USER_INPUT, data={"test": "data"}, source="test")

        # Hook values should still be None after registration
        asyncio.run(self.event_bus.register_hook(hook))
        self.assertIsNone(hook.timeout)
        self.assertIsNone(hook.retry_attempts)
        self.assertIsNone(hook.error_action)

        # But execution should succeed using config defaults (60, 5, "stop")
        result = asyncio.run(self.event_bus.hook_executor.execute_hook(hook, event))
        self.assertTrue(result["success"])

    def test_explicit_values_not_overridden(self):
        """Test that explicitly set values are not overridden by config."""
        # Create a hook with explicit non-default values
        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.PREPROCESSING.value,
            callback=AsyncMock(),
            timeout=10,  # Explicit value different from config
            retry_attempts=1,  # Explicit value different from config
            error_action="continue",  # Explicit value different from config
        )

        # Register the hook
        asyncio.run(self.event_bus.register_hook(hook))

        # Verify explicit values were preserved
        self.assertEqual(hook.timeout, 10)
        self.assertEqual(hook.retry_attempts, 1)
        self.assertEqual(hook.error_action, "continue")

    @patch("kollabor_events.executor.asyncio.sleep", new=_instant_sleep)
    def test_retry_logic_on_failure(self):
        """Test that hooks are retried on failure."""
        call_count = 0

        async def failing_callback(data, event):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Test error")
            return {"status": "success"}

        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.PREPROCESSING.value,
            callback=failing_callback,
            retry_attempts=2,
        )

        event = Event(type=EventType.USER_INPUT, data={"test": "data"}, source="test")

        # Execute hook
        result = asyncio.run(self.event_bus.hook_executor.execute_hook(hook, event))

        # Verify retry occurred
        self.assertEqual(call_count, 3)  # Initial attempt + 2 retries
        self.assertTrue(result["success"])
        self.assertEqual(result["retry_count"], 2)  # Succeeded on 3rd attempt (retry 2)
        self.assertEqual(len(result["attempts"]), 3)

    @patch("kollabor_events.executor.asyncio.sleep", new=_instant_sleep)
    def test_retry_logic_exhausted(self):
        """Test that hook fails after exhausting retries."""
        call_count = 0

        async def always_failing_callback(data, event):
            nonlocal call_count
            call_count += 1
            raise ValueError("Test error")

        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.PREPROCESSING.value,
            callback=always_failing_callback,
            retry_attempts=2,
        )

        event = Event(type=EventType.USER_INPUT, data={"test": "data"}, source="test")

        # Execute hook
        result = asyncio.run(self.event_bus.hook_executor.execute_hook(hook, event))

        # Verify all retries were attempted
        self.assertEqual(call_count, 3)  # Initial attempt + 2 retries
        self.assertFalse(result["success"])
        self.assertEqual(result["retry_count"], 2)
        self.assertEqual(result["error"], "Test error")
        self.assertEqual(len(result["attempts"]), 3)
        self.assertEqual(hook.status, HookStatus.FAILED)

    def test_timeout_with_retries(self):
        """Test that timeouts trigger retries."""
        call_count = 0
        real_sleep = asyncio.sleep

        async def timeout_callback(data, event):
            nonlocal call_count
            call_count += 1
            await real_sleep(10)  # Will timeout (uses real sleep, not patched)
            return {"status": "success"}

        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.PREPROCESSING.value,
            callback=timeout_callback,
            timeout=1,  # 1s timeout (minimum enforced by executor)
            retry_attempts=2,
        )

        event = Event(type=EventType.USER_INPUT, data={"test": "data"}, source="test")

        # Patch backoff sleep to be instant, but let the callback sleep stay real (so it times out)
        original_sleep = asyncio.sleep

        async def fast_backoff(delay):
            await original_sleep(0)

        with patch("kollabor_events.executor.asyncio.sleep", side_effect=fast_backoff):
            result = asyncio.run(self.event_bus.hook_executor.execute_hook(hook, event))

        # Verify retries occurred on timeout
        self.assertEqual(call_count, 3)  # Initial attempt + 2 retries
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "timeout")
        self.assertEqual(len(result["attempts"]), 3)
        self.assertTrue(all(a["error"] == "timeout" for a in result["attempts"]))
        self.assertEqual(hook.status, HookStatus.TIMEOUT)

    def test_no_config_uses_defaults(self):
        """Test that missing config uses hardcoded defaults during execution."""
        # Create event bus without config
        event_bus = EventBus()

        async def quick_callback(data, event):
            return {"status": "success"}

        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.PREPROCESSING.value,
            callback=quick_callback,
        )

        event = Event(type=EventType.USER_INPUT, data={"test": "data"}, source="test")

        # Register the hook - values still None
        asyncio.run(event_bus.register_hook(hook))
        self.assertIsNone(hook.timeout)
        self.assertIsNone(hook.retry_attempts)
        self.assertIsNone(hook.error_action)

        # But execution should succeed using hardcoded defaults (30, 3, "continue")
        result = asyncio.run(event_bus.hook_executor.execute_hook(hook, event))
        self.assertTrue(result["success"])

    def test_error_action_stop_cancels_event(self):
        """Test that error_action='stop' cancels the event on failure."""

        async def failing_callback(data, event):
            raise ValueError("Test error")

        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.PREPROCESSING.value,
            callback=failing_callback,
            retry_attempts=0,
            error_action="stop",
        )

        event = Event(type=EventType.USER_INPUT, data={"test": "data"}, source="test")

        # Execute hook
        result = asyncio.run(self.event_bus.hook_executor.execute_hook(hook, event))

        # Verify event was cancelled
        self.assertTrue(event.cancelled)
        self.assertFalse(result["success"])
        self.assertEqual(hook.status, HookStatus.FAILED)

    def test_invalid_timeout_clamped_to_minimum(self):
        """Test that invalid timeout values are clamped to minimum."""
        config = {
            "hooks": {
                "default_timeout": -10,  # Invalid negative value
                "default_retries": 3,
                "default_error_action": "continue",
            }
        }
        event_bus = EventBus(config=config)

        async def quick_callback(data, event):
            return {"status": "success"}

        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.PREPROCESSING.value,
            callback=quick_callback,
        )

        event = Event(type=EventType.USER_INPUT, data={"test": "data"}, source="test")

        # Execute hook - should clamp to MIN_TIMEOUT (1)
        result = asyncio.run(event_bus.hook_executor.execute_hook(hook, event))

        # Should succeed despite invalid config
        self.assertTrue(result["success"])

    @patch("kollabor_events.executor.asyncio.sleep", new=_instant_sleep)
    def test_retry_attempts_capped_at_absolute_max(self):
        """Test that retry attempts are capped at absolute maximum."""
        config = {
            "hooks": {
                "default_timeout": 30,
                "default_retries": 999999,  # Malicious huge value
                "default_error_action": "continue",
            }
        }
        event_bus = EventBus(config=config)

        call_count = 0

        async def failing_callback(data, event):
            nonlocal call_count
            call_count += 1
            raise ValueError("Test error")

        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.PREPROCESSING.value,
            callback=failing_callback,
        )

        event = Event(type=EventType.USER_INPUT, data={"test": "data"}, source="test")

        # Execute hook - should cap retries at ABSOLUTE_MAX_RETRIES (10)
        result = asyncio.run(event_bus.hook_executor.execute_hook(hook, event))

        # Should only attempt 11 times (1 initial + 10 retries), not 1000000
        self.assertEqual(call_count, 11)
        self.assertFalse(result["success"])

    def test_invalid_error_action_defaults_to_continue(self):
        """Test that invalid error_action values default to 'continue'."""
        config = {
            "hooks": {
                "default_timeout": 30,
                "default_retries": 0,
                "default_error_action": "invalid_typo",  # Invalid value
            }
        }
        event_bus = EventBus(config=config)

        async def failing_callback(data, event):
            raise ValueError("Test error")

        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.PREPROCESSING.value,
            callback=failing_callback,
        )

        event = Event(type=EventType.USER_INPUT, data={"test": "data"}, source="test")

        # Execute hook
        result = asyncio.run(event_bus.hook_executor.execute_hook(hook, event))

        # Should fail but NOT cancel event (defaults to "continue")
        self.assertFalse(result["success"])
        self.assertFalse(event.cancelled)

    def test_string_timeout_value_falls_back_to_default(self):
        """Test that string timeout values are rejected and use default."""
        config = {
            "hooks": {
                "default_timeout": "not_a_number",  # Invalid type
                "default_retries": 3,
                "default_error_action": "continue",
            }
        }
        event_bus = EventBus(config=config)

        async def quick_callback(data, event):
            return {"status": "success"}

        hook = Hook(
            name="test_hook",
            plugin_name="test_plugin",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.PREPROCESSING.value,
            callback=quick_callback,
        )

        event = Event(type=EventType.USER_INPUT, data={"test": "data"}, source="test")

        # Execute hook - should use MIN_TIMEOUT as fallback
        result = asyncio.run(event_bus.hook_executor.execute_hook(hook, event))

        # Should succeed with fallback timeout
        self.assertTrue(result["success"])


if __name__ == "__main__":
    unittest.main()
