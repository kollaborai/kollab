"""Tests for the HookAdapter removal (issue #205).

HookAdapter was a migration shim for a pre-migration Hook format
(`plugin=<instance>`). The current Hook dataclass has no `plugin` field,
no production path registered through the adapter, and old-format
construction raises TypeError at the model level. These tests pin the
post-removal contract:

1. kollabor_events imports cleanly without the shim and no longer
   exports HookAdapter.
2. Old-format hooks can NOT silently pass registration: constructing
   one fails loudly (TypeError), and direct bus/registry registration
   of a duck-typed old-format hook is rejected loudly (AttributeError),
   never a silent no-op success.
3. New-format registration is unaffected.
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import kollabor_events
from kollabor_events.bus import EventBus
from kollabor_events.models import Hook
from kollabor_events.registry import HookRegistry


def _run(coro):
    try:
        loop = asyncio.new_event_loop()
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _new_format_hook(**overrides):
    params = {
        "name": "sample_hook",
        "plugin_name": "sample_plugin",
        "event_type": "plugin:sample",
        "priority": 50,
        "callback": lambda event: None,
    }
    params.update(overrides)
    return Hook(**params)


class TestHookAdapterRemoved(unittest.TestCase):
    """The shim is gone from the public package."""

    def test_package_imports_cleanly(self):
        import importlib

        module = importlib.import_module("kollabor_events")
        importlib.reload(module)
        self.assertTrue(hasattr(module, "HookRegistry"))
        self.assertTrue(hasattr(module, "HookExecutor"))
        self.assertTrue(hasattr(module, "EventBus") or hasattr(module, "bus"))

    def test_hook_adapter_not_exported(self):
        self.assertFalse(
            hasattr(kollabor_events, "HookAdapter"),
            "HookAdapter should no longer be exported from kollabor_events",
        )
        self.assertNotIn(
            "HookAdapter",
            getattr(kollabor_events, "__all__", []),
        )

    def test_hook_adapter_module_gone(self):
        with self.assertRaises(ImportError):
            import kollabor_events.hook_adapter  # noqa: F401


class TestOldFormatFailsLoudly(unittest.TestCase):
    """Old-format hooks must crash at the edge, not pass silently."""

    def test_old_format_construction_raises_type_error(self):
        with self.assertRaises(TypeError):
            Hook(
                name="legacy",
                plugin=object(),  # old format: instance reference
                event_type="plugin:legacy",
                priority=50,
                callback=lambda event: None,
            )

    def test_duck_typed_old_format_rejected_by_registry(self):
        """A duck-typed old-format hook fails registration loudly."""
        registry = HookRegistry()

        class LegacyHook:
            name = "legacy_hook"
            plugin = object()  # old format, no plugin_name

        legacy = LegacyHook()
        with self.assertRaises(AttributeError):
            registry.register_hook(legacy)

    def test_duck_typed_old_format_rejected_by_bus(self):
        """Same loud failure through the event bus entry point."""

        class LegacyHook:
            name = "legacy_hook"
            plugin = object()

        bus = EventBus.__new__(EventBus)  # bypass heavy wiring; registry only
        bus.hook_registry = HookRegistry()

        async def register():
            return await bus.register_hook(LegacyHook())

        with self.assertRaises(AttributeError):
            _run(register())

    def test_missing_plugin_name_new_style_hook_rejected(self):
        """New-style hooks without plugin_name are also loud, not silent."""
        registry = HookRegistry()

        class BadHook:
            name = "bad_hook"
            # no plugin_name, no plugin

        with self.assertRaises(AttributeError):
            registry.register_hook(BadHook())


class TestNewFormatRegistrationUnaffected(unittest.TestCase):
    """The migration must not have changed the supported path."""

    def test_new_format_hook_registers_and_unregisters(self):
        registry = HookRegistry()
        hook = _new_format_hook()
        self.assertTrue(registry.register_hook(hook))
        self.assertTrue(registry.unregister_hook("sample_plugin", "sample_hook"))

    def test_duplicate_registration_updates_not_crashes(self):
        registry = HookRegistry()
        first = _new_format_hook()
        second = _new_format_hook()
        self.assertTrue(registry.register_hook(first))
        self.assertTrue(registry.register_hook(second))


if __name__ == "__main__":
    unittest.main()
