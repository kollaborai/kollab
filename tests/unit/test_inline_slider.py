"""Unit tests for InlineSliderEditor.

Tests the inline slider widget functionality including:
- Left/Right arrows adjust value by step
- Up/Down arrows adjust value by 10x step
- 1-9 keys jump to preset N
- Enter confirms and saves
- Esc cancels and reverts
- Value clamping to min/max range
"""

import re
import unittest

from kollabor_tui.key_parser import KeyPress, KeyType
from kollabor_tui.status.inline_editors import EditorResult, InlineSliderEditor


def strip_ansi(text):
    """Strip ANSI escape codes from text for testing."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestInlineSliderEditor(unittest.TestCase):
    """Test suite for InlineSliderEditor class."""

    def setUp(self):
        """Set up test fixtures."""
        self.presets = [0.1, 0.5, 0.7, 1.0, 1.5]
        self.editor = InlineSliderEditor(
            value=0.7,
            min_val=0.0,
            max_val=2.0,
            step=0.1,
            presets=self.presets,
            label="temp",
            width=35,
            bar_width=15,
        )

    def test_initialization(self):
        """Test editor initializes correctly."""
        self.assertEqual(self.editor.get_value(), 0.7)
        self.assertEqual(self.editor._min_val, 0.0)
        self.assertEqual(self.editor._max_val, 2.0)
        self.assertEqual(self.editor._step, 0.1)
        self.assertFalse(self.editor.is_confirmed())
        self.assertFalse(self.editor.is_cancelled())
        self.assertFalse(self.editor.is_done())

    def test_render_contains_current_value(self):
        """Test render output contains current value."""
        output = self.editor.render()
        self.assertIn("0.7", output)
        self.assertIn("temp", output)

    def test_render_shows_progress_bar(self):
        """Test render shows a progress bar representation."""
        output = self.editor.render()
        # Should contain brackets for the slider bar
        self.assertIn("[", output)
        self.assertIn("]", output)

    def test_left_arrow_decreases_value(self):
        """Test ArrowLeft key decreases value by step."""
        # Start at 0.7
        self.assertEqual(self.editor.get_value(), 0.7)

        # Press left arrow
        key = KeyPress(name="ArrowLeft", code=1, type=KeyType.CONTROL)
        handled = self.editor.handle_keypress(key)

        self.assertTrue(handled)
        self.assertEqual(self.editor.get_value(), 0.6)  # Decreased by 0.1

    def test_right_arrow_increases_value(self):
        """Test ArrowRight key increases value by step."""
        # Start at 0.7
        self.assertEqual(self.editor.get_value(), 0.7)

        # Press right arrow
        key = KeyPress(name="ArrowRight", code=2, type=KeyType.CONTROL)
        handled = self.editor.handle_keypress(key)

        self.assertTrue(handled)
        self.assertAlmostEqual(
            self.editor.get_value(), 0.8, places=5
        )  # Increased by 0.1

    def test_up_arrow_increases_by_10x_step(self):
        """Test ArrowUp key increases value by 10x step."""
        # Start at 0.7
        self.assertEqual(self.editor.get_value(), 0.7)

        # Press up arrow (should add 1.0 = 10 * 0.1)
        key = KeyPress(name="ArrowUp", code=3, type=KeyType.CONTROL)
        handled = self.editor.handle_keypress(key)

        self.assertTrue(handled)
        self.assertAlmostEqual(
            self.editor.get_value(), 1.7, places=5
        )  # Increased by 1.0

    def test_down_arrow_decreases_by_10x_step(self):
        """Test ArrowDown key decreases value by 10x step."""
        # Set to 1.7 first
        self.editor.set_value(1.7)

        # Press down arrow (should subtract 1.0 = 10 * 0.1)
        key = KeyPress(name="ArrowDown", code=4, type=KeyType.CONTROL)
        handled = self.editor.handle_keypress(key)

        self.assertTrue(handled)
        self.assertAlmostEqual(
            self.editor.get_value(), 0.7, places=5
        )  # Decreased by 1.0

    def test_value_clamps_to_minimum(self):
        """Test value is clamped to minimum when decreasing below min."""
        # Start at 0.1 (near minimum)
        self.editor.set_value(0.1)

        # Press left arrow to go below minimum
        key = KeyPress(name="ArrowLeft", code=1, type=KeyType.CONTROL)
        self.editor.handle_keypress(key)

        # Should clamp to 0.0 (minimum), not go below
        self.assertEqual(self.editor.get_value(), 0.0)

    def test_value_clamps_to_maximum(self):
        """Test value is clamped to maximum when increasing above max."""
        # Start at 1.9 (near maximum)
        self.editor.set_value(1.9)

        # Press right arrow to go above maximum
        key = KeyPress(name="ArrowRight", code=2, type=KeyType.CONTROL)
        self.editor.handle_keypress(key)

        # Should clamp to 2.0 (maximum), not exceed
        self.assertEqual(self.editor.get_value(), 2.0)

    def test_digit_key_jumps_to_preset(self):
        """Test 1-9 keys jump to corresponding preset."""
        # Start at 0.7
        self.assertEqual(self.editor.get_value(), 0.7)

        # Press '1' for first preset (0.1)
        key = KeyPress(name="1", code=49, char="1", type=KeyType.PRINTABLE)
        handled = self.editor.handle_keypress(key)

        self.assertTrue(handled)
        self.assertEqual(self.editor.get_value(), 0.1)

        # Press '5' for fifth preset (1.5)
        key5 = KeyPress(name="5", code=53, char="5", type=KeyType.PRINTABLE)
        self.editor.handle_keypress(key5)

        self.assertEqual(self.editor.get_value(), 1.5)

    def test_digit_key_out_of_range_ignored(self):
        """Test digit keys beyond preset range are handled gracefully."""
        # Start at 0.7
        original_value = self.editor.get_value()

        # Press '9' when we only have 5 presets
        key = KeyPress(name="9", code=57, char="9", type=KeyType.PRINTABLE)
        handled = self.editor.handle_keypress(key)

        # Should NOT be handled (out of range)
        self.assertFalse(handled)
        self.assertEqual(self.editor.get_value(), original_value)

    def test_digit_key_without_presets_ignored(self):
        """Test digit keys do nothing when no presets defined."""
        # Create editor without presets
        editor_no_presets = InlineSliderEditor(
            value=1.0,
            min_val=0.0,
            max_val=2.0,
            step=0.1,
            presets=[],  # No presets
        )

        original_value = editor_no_presets.get_value()

        # Press '1'
        key = KeyPress(name="1", code=49, char="1", type=KeyType.PRINTABLE)
        handled = editor_no_presets.handle_keypress(key)

        # Should not be handled (no presets)
        self.assertFalse(handled)
        self.assertEqual(editor_no_presets.get_value(), original_value)

    def test_enter_confirms_value(self):
        """Test Enter key confirms the current value."""
        # Change value to 1.5
        self.editor.set_value(1.5)

        # Press Enter
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
        self.assertEqual(result.value, 1.5)

    def test_escape_cancels_and_reverts_to_original(self):
        """Test Escape key cancels and restores original value."""
        # Original value is 0.7 (set in setUp)
        original_value = 0.7

        # Change to 1.5
        self.editor.set_value(1.5)
        self.assertEqual(self.editor.get_value(), 1.5)

        # Press Escape
        key = KeyPress(name="Escape", code=27, type=KeyType.CONTROL)
        handled = self.editor.handle_keypress(key)

        self.assertTrue(handled)
        self.assertTrue(self.editor.is_cancelled())
        self.assertTrue(self.editor.is_done())
        self.assertFalse(self.editor.is_confirmed())

        # Should restore to original value
        self.assertEqual(self.editor.get_value(), original_value)

        # Check result
        result = self.editor.get_result()
        self.assertFalse(result.confirmed)
        self.assertEqual(result.value, original_value)

    def test_set_value_clamps_to_range(self):
        """Test set_value clamps to valid range."""
        # Set below minimum
        self.editor.set_value(-0.5)
        self.assertEqual(self.editor.get_value(), 0.0)  # Clamped to min

        # Set above maximum
        self.editor.set_value(3.0)
        self.assertEqual(self.editor.get_value(), 2.0)  # Clamped to max

    def test_set_value_with_valid_value(self):
        """Test set_value works with valid value."""
        self.editor.set_value(1.2)
        self.assertEqual(self.editor.get_value(), 1.2)

    def test_render_with_integer_value(self):
        """Test render displays integer values without decimal."""
        # Create editor with integer-like values
        editor = InlineSliderEditor(
            value=1.0,
            min_val=0,
            max_val=10,
            step=1.0,
        )

        output = editor.render()
        clean_output = strip_ansi(output)
        # Should show "1" not "1.0"
        self.assertIn(" 1", clean_output)  # Space + value

    def test_render_with_different_step_precision(self):
        """Test render adapts to step precision."""
        # Small step - should show more decimals
        editor_small = InlineSliderEditor(
            value=0.123,
            min_val=0.0,
            max_val=1.0,
            step=0.001,
        )
        output_small = editor_small.render()
        clean_output = strip_ansi(output_small)
        # Should show the value with at least 1 decimal
        self.assertIn("0.", clean_output)

    def test_invalid_range_raises_value_error(self):
        """Test that invalid range raises ValueError."""
        with self.assertRaises(ValueError):
            InlineSliderEditor(
                value=0.5,
                min_val=1.0,  # Min > Max
                max_val=0.0,
                step=0.1,
            )

    def test_zero_step_raises_value_error(self):
        """Test that zero step raises ValueError."""
        with self.assertRaises(ValueError):
            InlineSliderEditor(
                value=0.5,
                min_val=0.0,
                max_val=1.0,
                step=0.0,  # Invalid
            )

    def test_negative_step_raises_value_error(self):
        """Test that negative step raises ValueError."""
        with self.assertRaises(ValueError):
            InlineSliderEditor(
                value=0.5,
                min_val=0.0,
                max_val=1.0,
                step=-0.1,  # Invalid
            )

    def test_unhandled_key_returns_false(self):
        """Test unhandled keys return False."""
        # Some random key that's not handled
        key = KeyPress(name="Tab", code=9, type=KeyType.CONTROL)
        handled = self.editor.handle_keypress(key)

        self.assertFalse(handled)

    def test_render_includes_preset_hint(self):
        """Test render includes hint when presets are available."""
        output = self.editor.render()
        # Should hint that 1-9 keys work for presets
        self.assertIn("[1-9]", output)

    def test_render_without_presets_no_hint(self):
        """Test render has no preset hint when no presets."""
        editor_no_presets = InlineSliderEditor(
            value=1.0,
            min_val=0.0,
            max_val=2.0,
            step=0.1,
            presets=[],  # No presets
        )

        output = editor_no_presets.render()
        # Should NOT show preset hint
        self.assertNotIn("[1-9]", output)


class TestInlineSliderEditorEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions."""

    def test_min_equals_max_raises_value_error(self):
        """Test editor raises ValueError when min equals max (not a valid range)."""
        # The implementation requires min < max (strictly)
        with self.assertRaises(ValueError):
            InlineSliderEditor(
                value=5.0,
                min_val=5.0,
                max_val=5.0,
                step=0.1,
            )

    def test_very_small_step(self):
        """Test editor works with very small step size."""
        editor = InlineSliderEditor(
            value=0.5,
            min_val=0.0,
            max_val=1.0,
            step=0.001,
        )

        right_key = KeyPress(name="ArrowRight", code=2, type=KeyType.CONTROL)
        editor.handle_keypress(right_key)

        # Should increase by 0.001
        self.assertAlmostEqual(editor.get_value(), 0.501, places=5)

    def test_large_step(self):
        """Test editor works with large step size."""
        editor = InlineSliderEditor(
            value=50.0,
            min_val=0.0,
            max_val=100.0,
            step=10.0,
        )

        right_key = KeyPress(name="ArrowRight", code=2, type=KeyType.CONTROL)
        editor.handle_keypress(right_key)

        self.assertEqual(editor.get_value(), 60.0)

        # Up arrow should add 100 (10x step)
        up_key = KeyPress(name="ArrowUp", code=3, type=KeyType.CONTROL)
        editor.handle_keypress(up_key)

        # Should clamp to max (100.0)
        self.assertEqual(editor.get_value(), 100.0)


if __name__ == "__main__":
    unittest.main()
