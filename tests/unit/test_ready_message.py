"""Unit tests for ready message collection system."""

import asyncio
import sys
import unittest
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from kollabor_events.ready_message import ReadyMessageCollector, ReadyMessageItem


class TestReadyMessageItem(unittest.TestCase):
    """Test ReadyMessageItem dataclass."""

    def test_format_with_count(self):
        """Test formatting with numeric count."""
        item = ReadyMessageItem(
            category="system prompt",
            count=24,
            label="modules",
            priority=1000,
            source="core",
        )
        self.assertEqual(item.format(), "24 system prompt modules")

    def test_format_without_count(self):
        """Test formatting without numeric count."""
        item = ReadyMessageItem(
            category="agent mode",
            count=None,
            label="enabled",
            priority=700,
            source="agent_plugin",
        )
        self.assertEqual(item.format(), "agent mode enabled")

    def test_priority_default(self):
        """Test default priority value."""
        item = ReadyMessageItem(category="test", count=1, label="item", source="test")
        self.assertEqual(item.priority, 100)


class TestReadyMessageCollector(unittest.TestCase):
    """Test ReadyMessageCollector functionality."""

    def setUp(self):
        """Create a fresh collector for each test."""
        self.collector = ReadyMessageCollector()

    def test_add_single_item(self):
        """Test adding a single ready message item."""
        self.collector.add(
            category="hooks", count=85, label="active", priority=900, source="core"
        )
        self.assertEqual(self.collector.get_count(), 1)

    def test_add_multiple_items(self):
        """Test adding multiple ready message items."""
        self.collector.add("system prompt", 24, "modules", priority=1000, source="core")
        self.collector.add("hooks", 85, "active", priority=900, source="core")
        self.collector.add("plugins", 15, "loaded", priority=800, source="core")

        self.assertEqual(self.collector.get_count(), 3)

    def test_get_formatted_messages_sorted_by_priority(self):
        """Test that formatted messages are sorted by priority (highest first)."""
        # Add items in random priority order
        self.collector.add("low", 1, "priority", priority=100, source="test")
        self.collector.add("high", 3, "priority", priority=1000, source="test")
        self.collector.add("medium", 2, "priority", priority=500, source="test")

        messages = self.collector.get_formatted_messages()

        # Should be sorted highest to lowest priority
        self.assertEqual(messages[0], "3 high priority")
        self.assertEqual(messages[1], "2 medium priority")
        self.assertEqual(messages[2], "1 low priority")

    def test_format_for_display_with_limit(self):
        """Test formatting with max_items limit."""
        # Add 5 items
        for i in range(5):
            self.collector.add(
                category=f"category{i}",
                count=i + 1,
                label="items",
                priority=1000 - i * 100,
                source="test",
            )

        # Request only top 3
        result = self.collector.format_for_display(max_items=3)

        # Should contain first 3, comma-separated
        self.assertIn("1 category0 items", result)
        self.assertIn("2 category1 items", result)
        self.assertIn("3 category2 items", result)

        # Should indicate 2 more items
        self.assertIn("and 2 more", result)

    def test_format_for_display_no_truncation(self):
        """Test formatting when items <= max_items."""
        self.collector.add("item1", 1, "test", priority=100, source="test")
        self.collector.add("item2", 2, "test", priority=90, source="test")

        result = self.collector.format_for_display(max_items=5)

        # Should not have "and X more"
        self.assertNotIn("more", result)

    def test_format_for_display_empty(self):
        """Test formatting with no items."""
        result = self.collector.format_for_display()
        self.assertEqual(result, "")

    def test_clear(self):
        """Test clearing all collected items."""
        self.collector.add("test", 1, "item", priority=100, source="test")
        self.assertEqual(self.collector.get_count(), 1)

        self.collector.clear()
        self.assertEqual(self.collector.get_count(), 0)

    def test_core_stats_pattern(self):
        """Test the typical core stats pattern."""
        # Simulate what core would add
        self.collector.add("system prompt", 24, "modules", priority=1000, source="core")
        self.collector.add("hooks", 85, "active", priority=900, source="core")
        self.collector.add("plugins", 15, "loaded", priority=800, source="core")
        self.collector.add("status views", 8, "available", priority=700, source="core")

        result = self.collector.format_for_display(max_items=6)

        # Should be formatted correctly
        expected = (
            "24 system prompt modules, 85 hooks active, "
            "15 plugins loaded, 8 status views available"
        )
        self.assertEqual(result, expected)

    def test_plugin_contribution_pattern(self):
        """Test plugin contributing additional stats."""
        # Core stats
        self.collector.add("system prompt", 24, "modules", priority=1000, source="core")
        self.collector.add("hooks", 85, "active", priority=900, source="core")

        # Plugin contribution
        self.collector.add(
            "MCP servers", 3, "connected", priority=650, source="mcp_plugin"
        )

        messages = self.collector.get_formatted_messages()

        # All three should be present, sorted by priority
        self.assertEqual(messages[0], "24 system prompt modules")
        self.assertEqual(messages[1], "85 hooks active")
        self.assertEqual(messages[2], "3 MCP servers connected")

    def test_non_numeric_stat(self):
        """Test stats without numeric counts."""
        self.collector.add("agent mode", None, "enabled", priority=700, source="agent")

        messages = self.collector.get_formatted_messages()
        self.assertEqual(messages[0], "agent mode enabled")

    def test_mixed_numeric_and_non_numeric(self):
        """Test mixing numeric and non-numeric stats."""
        self.collector.add("hooks", 85, "active", priority=900, source="core")
        self.collector.add("agent mode", None, "enabled", priority=700, source="agent")

        result = self.collector.format_for_display(max_items=5)
        self.assertIn("85 hooks active", result)
        self.assertIn("agent mode enabled", result)


def run_async_test(coro):
    """Helper to run async tests."""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


if __name__ == "__main__":
    unittest.main()
