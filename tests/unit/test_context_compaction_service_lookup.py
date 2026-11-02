"""Regression tests for the #517 context-compaction service lookup.

The application used to look the plugin up under a key the plugin factory
never stores ("context_compaction"), so the service only registered through
the class-name compatibility fallback. These tests pin the contract from the
factory side: the lookup key must be a key the REAL factory actually
produces for the REAL plugin class, not a hand-built instance map.
"""

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kollabor_events.bus import EventBus
from kollabor_plugins.factory import PluginFactory
from plugins.context_compaction_plugin import ContextCompactionPlugin


def _factory_instances_for_real_plugin():
    factory = PluginFactory()
    factory.instantiate_plugin(
        ContextCompactionPlugin,
        "ContextCompactionPlugin",
        EventBus(),
        MagicMock(),
        MagicMock(),
    )
    return factory.plugin_instances


class TestContextCompactionServiceLookup(unittest.TestCase):
    """#517: the service must resolve through the real factory path."""

    def test_factory_stores_real_plugin_under_clean_name(self):
        instances = _factory_instances_for_real_plugin()
        self.assertIn("ContextCompactionPlugin", instances)
        self.assertIn("contextcompaction", instances)
        self.assertIsInstance(
            instances["contextcompaction"], ContextCompactionPlugin
        )
        # The historical primary lookup key was never a factory key.
        self.assertNotIn("context_compaction", instances)

    def test_application_primary_lookup_key_is_the_factory_clean_name(self):
        source = (REPO_ROOT / "kollabor" / "application.py").read_text(
            encoding="utf-8"
        )
        match = re.search(
            r"context_compaction_plugin = self\.plugin_instances\.get\(\s*\"([^\"]+)\"",
            source,
        )
        self.assertIsNotNone(match, "context compaction lookup block not found")
        self.assertEqual(match.group(1), "contextcompaction")

    def test_service_registers_through_real_factory_and_bus(self):
        instances = _factory_instances_for_real_plugin()
        bus = EventBus()
        plugin = instances.get("contextcompaction") or instances.get(
            "ContextCompactionPlugin"
        )
        self.assertIsNotNone(plugin)
        bus.register_service("context_compaction_plugin", plugin)
        self.assertIs(bus.get_service("context_compaction_plugin"), plugin)


if __name__ == "__main__":
    unittest.main()
