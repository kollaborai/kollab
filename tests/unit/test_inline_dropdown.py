"""Unit tests for InlineDropdownEditor.

Tests the inline dropdown widget functionality including:
- Arrow key navigation (up/down)
- 1-9 quick select
- Character jump (type letter to jump to option)
- Enter/Esc confirm/cancel
"""

import unittest

from kollabor_tui.key_parser import KeyPress, KeyType
from kollabor_tui.status.inline_editors import EditorResult, InlineDropdownEditor


class TestInlineDropdownEditor(unittest.TestCase):
    """Test suite for InlineDropdownEditor class."""

    def setUp(self):
        """Set up test fixtures."""
        self.options = ["alpha", "beta", "charlie", "delta", "echo", "foxtrot", "golf"]
        self.editor = InlineDropdownEditor(
            options=self.options,
            selected_index=0,
            label="test",
            width=30,
        )

    def test_initialization(self):
        """Test editor initializes correctly."""
        self.assertEqual(self.editor.get_value(), "alpha")
        self.assertEqual(self.editor._selected_index, 0)
        self.assertFalse(self.editor.is_confirmed())
        self.assertFalse(self.editor.is_cancelled())
        self.assertFalse(self.editor.is_done())

    def test_render_contains_current_selection(self):
        """Test render output contains current selection."""
        output = self.editor.render()
        self.assertIn("alpha", output)
        self.assertIn("▼", output)  # Dropdown arrow
        self.assertIn("[1/7]", output)  # Counter

    def test_arrow_up_navigates_backward(self):
        """Test ArrowUp key navigates to previous option (wraps)."""
        self.editor._selected_index = 2  # Start at "charlie"

        # Create ArrowUp key press
        key = KeyPress(name="ArrowUp", code=1, type=KeyType.CONTROL)
        handled = self.editor.handle_keypress(key)

        self.assertTrue(handled)
        self.assertEqual(self.editor.get_value(), "beta")
        self.assertEqual(self.editor._selected_index, 1)

        # Test wraparound from first to last
        self.editor._selected_index = 0
        self.editor.handle_keypress(key)
        self.assertEqual(self.editor.get_value(), "golf")  # Last option
        self.assertEqual(self.editor._selected_index, 6)

    def test_arrow_down_navigates_forward(self):
        """Test ArrowDown key navigates to next option (wraps)."""
        self.editor._selected_index = 0  # Start at "alpha"

        # Create ArrowDown key press
        key = KeyPress(name="ArrowDown", code=2, type=KeyType.CONTROL)
        handled = self.editor.handle_keypress(key)

        self.assertTrue(handled)
        self.assertEqual(self.editor.get_value(), "beta")
        self.assertEqual(self.editor._selected_index, 1)

        # Test wraparound from last to first
        self.editor._selected_index = 6
        self.editor.handle_keypress(key)
        self.assertEqual(self.editor.get_value(), "alpha")  # First option
        self.assertEqual(self.editor._selected_index, 0)

    def test_quick_select_with_digit_keys(self):
        """Test 1-9 keys quick select options (and auto-confirm)."""
        # Press '2' to select option at index 1 (beta)
        key = KeyPress(name="2", code=50, char="2", type=KeyType.PRINTABLE)
        handled = self.editor.handle_keypress(key)

        self.assertTrue(handled)
        self.assertEqual(self.editor.get_value(), "beta")
        self.assertTrue(self.editor.is_confirmed())  # Auto-confirms
        self.assertTrue(self.editor.is_done())

    def test_quick_select_out_of_range_ignored(self):
        """Test digit keys beyond options range are handled gracefully."""
        # Press '9' when we only have 7 options
        key = KeyPress(name="9", code=57, char="9", type=KeyType.PRINTABLE)
        handled = self.editor.handle_keypress(key)

        self.assertTrue(handled)  # Handled but no change
        self.assertEqual(self.editor.get_value(), "alpha")  # Still at start
        self.assertFalse(self.editor.is_confirmed())

    def test_character_jump_to_matching_option(self):
        """Test typing a letter jumps to option starting with that letter."""
        self.editor._selected_index = 0  # Start at "alpha"

        # Press 'c' to jump to "charlie"
        key = KeyPress(name="c", code=99, char="c", type=KeyType.PRINTABLE)
        handled = self.editor.handle_keypress(key)

        self.assertTrue(handled)
        self.assertEqual(self.editor.get_value(), "charlie")
        self.assertEqual(self.editor._selected_index, 2)

    def test_character_jump_continues_from_current(self):
        """Test character jump finds next match after current position."""
        self.editor._selected_index = 2  # At "charlie"

        # Press 'd' should find "delta" (next option starting with 'd')
        key = KeyPress(name="d", code=100, char="d", type=KeyType.PRINTABLE)
        self.editor.handle_keypress(key)

        self.assertEqual(self.editor.get_value(), "delta")

    def test_character_jump_wraps_to_beginning(self):
        """Test character jump wraps to beginning if no match after current."""
        self.editor._selected_index = 6  # At "golf" (last option)

        # Press 'a' should find "alpha" (wraps to beginning)
        key = KeyPress(name="a", code=97, char="a", type=KeyType.PRINTABLE)
        self.editor.handle_keypress(key)

        self.assertEqual(self.editor.get_value(), "alpha")

    def test_character_jump_no_match_stays_put(self):
        """Test character jump with no match stays at current position."""
        self.editor._selected_index = 0  # At "alpha"

        # Press 'z' when no option starts with 'z'
        key = KeyPress(name="z", code=122, char="z", type=KeyType.PRINTABLE)
        self.editor.handle_keypress(key)

        # Should stay at alpha (handled but no change)
        self.assertEqual(self.editor.get_value(), "alpha")

    def test_enter_confirms_selection(self):
        """Test Enter key confirms selection."""
        self.editor._selected_index = 3  # At "delta"

        key = KeyPress(name="Enter", code=13, type=KeyType.CONTROL)
        handled = self.editor.handle_keypress(key)

        self.assertTrue(handled)
        self.assertTrue(self.editor.is_confirmed())
        self.assertTrue(self.editor.is_done())
        self.assertFalse(self.editor.is_cancelled())

        # Check result
        result = self.editor.get_result()
        self.assertIsInstance(result, EditorResult)
        self.assertTrue(result.confirmed)
        self.assertEqual(result.value, "delta")

    def test_escape_cancels_and_restores_original(self):
        """Test Escape key cancels and restores original value."""
        # Set original value to "alpha" (index 0)
        self.editor._selected_index = 3  # Now at "delta"

        key = KeyPress(name="Escape", code=27, type=KeyType.CONTROL)
        handled = self.editor.handle_keypress(key)

        self.assertTrue(handled)
        self.assertTrue(self.editor.is_cancelled())
        self.assertTrue(self.editor.is_done())
        self.assertFalse(self.editor.is_confirmed())

        # Should restore to original value "alpha"
        self.assertEqual(self.editor.get_value(), "alpha")

        # Check result
        result = self.editor.get_result()
        self.assertFalse(result.confirmed)
        self.assertEqual(result.value, "alpha")  # Original value

    def test_set_value_finds_option_index(self):
        """Test set_value finds the correct index."""
        self.editor.set_value("charlie")
        self.assertEqual(self.editor._selected_index, 2)
        self.assertEqual(self.editor.get_value(), "charlie")

    def test_set_value_unknown_does_nothing(self):
        """Test set_value with unknown option does nothing."""
        original_index = self.editor._selected_index
        self.editor.set_value("zebra")  # Not in options
        self.assertEqual(self.editor._selected_index, original_index)

    def test_render_truncates_long_options(self):
        """Test render truncates options that are too long."""
        long_options = [
            "very_long_option_name_that_exceeds_width",
            "short",
        ]
        editor = InlineDropdownEditor(
            options=long_options,
            selected_index=0,
            width=20,
        )

        output = editor.render()
        self.assertIn("…", output)  # Should contain ellipsis
        # Check for truncated version (partially preserved)
        self.assertIn("very_long_opt", output)  # Partial text before ellipsis
        # The original full text should NOT be present
        self.assertNotIn("very_long_option_name_that_exceeds_width", output)

    def test_render_expanded_returns_multiple_lines(self):
        """Test render_expanded returns list of lines."""
        lines = self.editor.render_expanded(max_visible=5)

        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 1)  # More than just the dropdown line
        self.assertLessEqual(len(lines), 6)  # At most max_visible + 1

        # First line should be the dropdown
        self.assertIn("▼", lines[0])

        # Subsequent lines should show options with selection indicator
        selected_line = None
        for i, line in enumerate(lines[1:], start=1):
            if "→" in line:  # Selection indicator
                selected_line = line
                break

        self.assertIsNotNone(selected_line, "Should have a selected option with →")

    def test_render_expanded_shows_selection_indicator(self):
        """Test render_expanded highlights selected option."""
        self.editor._selected_index = 2  # "charlie"

        lines = self.editor.render_expanded(max_visible=5)

        # Strip ANSI codes for text matching
        import re

        def strip_ansi(text):
            return re.sub(r"\x1b\[[0-9;]*m", "", text)

        # Skip first line (the dropdown itself), check option lines
        charlie_line = None
        for line in lines[1:]:  # Skip dropdown line
            clean_line = strip_ansi(line)
            if "charlie" in clean_line:
                charlie_line = clean_line
                break

        self.assertIsNotNone(charlie_line)
        self.assertIn("→", charlie_line)  # Should have selection indicator

    def test_get_value_with_empty_options(self):
        """Test get_value returns empty string when no options."""
        editor = InlineDropdownEditor(
            options=[],
            selected_index=0,
        )
        self.assertEqual(editor.get_value(), "")

    def test_navigation_with_single_option(self):
        """Test navigation with only one option works correctly."""
        editor = InlineDropdownEditor(
            options=["only_option"],
            selected_index=0,
        )

        # Arrow keys should work but stay on same option
        up_key = KeyPress(name="ArrowUp", code=1, type=KeyType.CONTROL)
        down_key = KeyPress(name="ArrowDown", code=2, type=KeyType.CONTROL)

        editor.handle_keypress(up_key)
        self.assertEqual(editor.get_value(), "only_option")

        editor.handle_keypress(down_key)
        self.assertEqual(editor.get_value(), "only_option")


if __name__ == "__main__":
    unittest.main()
