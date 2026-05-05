"""Tests for DisplayFilterRegistry in message_renderer.py."""

import unittest

from kollabor_tui.message_renderer import DisplayFilterRegistry, MessageType


class TestDisplayFilterRegistry(unittest.TestCase):
    """Test cases for DisplayFilterRegistry."""

    def setUp(self):
        """Clear registry before each test."""
        DisplayFilterRegistry.clear()

    def tearDown(self):
        """Clear registry after each test."""
        DisplayFilterRegistry.clear()

    def test_no_filters_passes_through(self):
        """Content passes through unchanged when no filters registered."""
        content = "Hello <test>world</test>"
        result = DisplayFilterRegistry.apply_filters(content, MessageType.ASSISTANT)
        self.assertEqual(result, content)

    def test_register_and_apply_filter(self):
        """Registered filter transforms content."""

        def strip_test_tags(content, msg_type):
            import re

            return re.sub(r"<test>.*?</test>", "", content)

        DisplayFilterRegistry.register("test_filter", strip_test_tags)

        content = "Hello <test>world</test> there"
        result = DisplayFilterRegistry.apply_filters(content, MessageType.ASSISTANT)
        self.assertEqual(result, "Hello  there")

    def test_filter_respects_message_types(self):
        """Filter only applies to specified message types."""

        def strip_test_tags(content, msg_type):
            import re

            return re.sub(r"<test>.*?</test>", "", content)

        DisplayFilterRegistry.register(
            "test_filter",
            strip_test_tags,
            message_types=[MessageType.ASSISTANT],
        )

        content = "Hello <test>world</test>"

        # Should filter ASSISTANT
        result = DisplayFilterRegistry.apply_filters(content, MessageType.ASSISTANT)
        self.assertEqual(result, "Hello ")

        # Should NOT filter USER
        result = DisplayFilterRegistry.apply_filters(content, MessageType.USER)
        self.assertEqual(result, content)

        # Should NOT filter SYSTEM
        result = DisplayFilterRegistry.apply_filters(content, MessageType.SYSTEM)
        self.assertEqual(result, content)

    def test_filter_applies_to_all_types_when_none(self):
        """Filter applies to all message types when message_types is None."""

        def strip_test_tags(content, msg_type):
            import re

            return re.sub(r"<test>.*?</test>", "", content)

        DisplayFilterRegistry.register(
            "test_filter", strip_test_tags, message_types=None
        )

        content = "Hello <test>world</test>"

        # Should filter all types
        for msg_type in [MessageType.ASSISTANT, MessageType.USER, MessageType.SYSTEM]:
            result = DisplayFilterRegistry.apply_filters(content, msg_type)
            self.assertEqual(result, "Hello ")

    def test_unregister_filter(self):
        """Unregistered filter no longer applies."""

        def strip_test_tags(content, msg_type):
            return content.replace("STRIP", "")

        DisplayFilterRegistry.register("test_filter", strip_test_tags)
        DisplayFilterRegistry.unregister("test_filter")

        content = "Hello STRIP world"
        result = DisplayFilterRegistry.apply_filters(content, MessageType.ASSISTANT)
        self.assertEqual(result, content)

    def test_unregister_nonexistent_filter(self):
        """Unregistering nonexistent filter does not raise."""
        # Should not raise
        DisplayFilterRegistry.unregister("nonexistent")

    def test_priority_ordering(self):
        """Filters execute in priority order (higher first)."""
        execution_order = []

        def filter_a(content, msg_type):
            execution_order.append("A")
            return content + "_A"

        def filter_b(content, msg_type):
            execution_order.append("B")
            return content + "_B"

        def filter_c(content, msg_type):
            execution_order.append("C")
            return content + "_C"

        # Register with different priorities
        DisplayFilterRegistry.register("filter_b", filter_b, priority=50)
        DisplayFilterRegistry.register("filter_a", filter_a, priority=100)  # highest
        DisplayFilterRegistry.register("filter_c", filter_c, priority=10)  # lowest

        result = DisplayFilterRegistry.apply_filters("X", MessageType.ASSISTANT)

        # Higher priority runs first
        self.assertEqual(execution_order, ["A", "B", "C"])
        self.assertEqual(result, "X_A_B_C")

    def test_filter_error_handling(self):
        """Filter errors are caught and logged, processing continues."""

        def failing_filter(content, msg_type):
            raise ValueError("Intentional failure")

        def working_filter(content, msg_type):
            return content + "_WORKED"

        DisplayFilterRegistry.register("failing", failing_filter, priority=100)
        DisplayFilterRegistry.register("working", working_filter, priority=50)

        # Should not raise, and working filter should still apply
        result = DisplayFilterRegistry.apply_filters("test", MessageType.ASSISTANT)
        self.assertEqual(result, "test_WORKED")

    def test_clear_removes_all_filters(self):
        """Clear removes all registered filters."""

        def filter_a(content, msg_type):
            return content + "_A"

        def filter_b(content, msg_type):
            return content + "_B"

        DisplayFilterRegistry.register("a", filter_a)
        DisplayFilterRegistry.register("b", filter_b)
        DisplayFilterRegistry.clear()

        result = DisplayFilterRegistry.apply_filters("test", MessageType.ASSISTANT)
        self.assertEqual(result, "test")

    def test_multiple_filters_chain(self):
        """Multiple filters chain their transformations."""

        def add_prefix(content, msg_type):
            return "PREFIX_" + content

        def add_suffix(content, msg_type):
            return content + "_SUFFIX"

        DisplayFilterRegistry.register("prefix", add_prefix, priority=100)
        DisplayFilterRegistry.register("suffix", add_suffix, priority=50)

        result = DisplayFilterRegistry.apply_filters("MIDDLE", MessageType.ASSISTANT)
        self.assertEqual(result, "PREFIX_MIDDLE_SUFFIX")


if __name__ == "__main__":
    unittest.main()
