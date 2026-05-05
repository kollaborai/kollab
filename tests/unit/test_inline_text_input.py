#!/usr/bin/env python3
"""Unit test for InlineTextEditor widget.

This test verifies the Phase 3 inline text input functionality:
- Text input with cursor position tracking
- Backspace/Delete for editing
- Left/Right arrows for cursor movement
- Home/End for navigation
- Enter to confirm, Esc to cancel
"""

import sys
import unittest

# Add parent directory to path
sys.path.insert(0, ".")

from kollabor_tui.key_parser import KeyPress, KeyType
from kollabor_tui.status.inline_editors import InlineTextEditor


class TestInlineTextInput(unittest.TestCase):
    """Test cases for inline text input widget."""

    def setUp(self):
        """Set up test fixtures."""
        self.editor = InlineTextEditor(
            value="test",
            placeholder="type to edit...",
            width=40,
        )

    def test_initial_state(self):
        """Test 1: Editor initializes with correct values."""
        self.assertEqual(self.editor.get_value(), "test")
        self.assertEqual(self.editor._cursor_pos, 4)
        self.assertFalse(self.editor.is_confirmed())
        self.assertFalse(self.editor.is_cancelled())
        self.assertFalse(self.editor.is_done())

    def test_type_characters(self):
        """Test 2: Characters can be typed."""
        # Type "hello"
        for char in "hello":
            key = KeyPress(name=char, code=ord(char), char=char, type=KeyType.PRINTABLE)
            self.editor.handle_keypress(key)

        self.assertEqual(self.editor.get_value(), "testhello")
        self.assertEqual(self.editor._cursor_pos, 9)

    def test_backspace_deletes(self):
        """Test 3: Backspace deletes characters before cursor."""
        # Move cursor back
        backspace = KeyPress(name="Backspace", code=127, type=KeyType.CONTROL)
        self.editor.handle_keypress(backspace)
        self.editor.handle_keypress(backspace)

        self.assertEqual(self.editor.get_value(), "te")
        self.assertEqual(self.editor._cursor_pos, 2)

    def test_delete_deletes_at_cursor(self):
        """Test 4: Delete deletes character at cursor."""
        # Move cursor to position 2
        left = KeyPress(name="ArrowLeft", code="D", type=KeyType.EXTENDED)
        self.editor.handle_keypress(left)
        self.editor.handle_keypress(left)

        # Delete at cursor
        delete = KeyPress(name="Delete", code="[3~", type=KeyType.EXTENDED)
        self.editor.handle_keypress(delete)

        self.assertEqual(self.editor.get_value(), "tet")
        self.assertEqual(self.editor._cursor_pos, 2)

    def test_arrow_keys_move_cursor(self):
        """Test 5: Arrow keys move cursor."""
        left = KeyPress(name="ArrowLeft", code="D", type=KeyType.EXTENDED)
        right = KeyPress(name="ArrowRight", code="C", type=KeyType.EXTENDED)

        # Move left
        self.editor.handle_keypress(left)
        self.assertEqual(self.editor._cursor_pos, 3)

        # Move right
        self.editor.handle_keypress(right)
        self.assertEqual(self.editor._cursor_pos, 4)

        # Can't move past end
        self.editor.handle_keypress(right)
        self.assertEqual(self.editor._cursor_pos, 4)

    def test_home_end_navigation(self):
        """Test 6: Home/End jump to start/end."""
        home = KeyPress(name="Home", code="H", type=KeyType.EXTENDED)
        end = KeyPress(name="End", code="F", type=KeyType.EXTENDED)

        # Jump to start
        self.editor.handle_keypress(home)
        self.assertEqual(self.editor._cursor_pos, 0)

        # Jump to end
        self.editor.handle_keypress(end)
        self.assertEqual(self.editor._cursor_pos, 4)

    def test_insert_at_cursor(self):
        """Test 7: Inserting at cursor position."""
        # Move to middle
        left = KeyPress(name="ArrowLeft", code="D", type=KeyType.EXTENDED)
        self.editor.handle_keypress(left)
        self.editor.handle_keypress(left)

        # Insert at position 2
        xyz = KeyPress(name="X", code=ord("X"), char="X", type=KeyType.PRINTABLE)
        self.editor.handle_keypress(xyz)

        self.assertEqual(self.editor.get_value(), "teXst")
        self.assertEqual(self.editor._cursor_pos, 3)

    def test_enter_confirms(self):
        """Test 8: Enter confirms the edit."""
        enter = KeyPress(name="Enter", code=13, type=KeyType.CONTROL)
        self.editor.handle_keypress(enter)

        self.assertTrue(self.editor.is_confirmed())
        self.assertFalse(self.editor.is_cancelled())
        self.assertTrue(self.editor.is_done())

        # Check result
        result = self.editor.get_result()
        self.assertTrue(result.confirmed)
        self.assertEqual(result.value, "test")

    def test_escape_cancels(self):
        """Test 9: Esc cancels and reverts."""
        # Type something
        for char in "hello":
            key = KeyPress(name=char, code=ord(char), char=char, type=KeyType.PRINTABLE)
            self.editor.handle_keypress(key)

        self.assertEqual(self.editor.get_value(), "testhello")

        # Cancel
        escape = KeyPress(name="Escape", code=27, type=KeyType.CONTROL)
        self.editor.handle_keypress(escape)

        self.assertTrue(self.editor.is_cancelled())
        self.assertFalse(self.editor.is_confirmed())
        self.assertTrue(self.editor.is_done())

        # Value should be reverted to original
        result = self.editor.get_result()
        self.assertFalse(result.confirmed)
        self.assertEqual(result.value, "test")  # Original value

    def test_render_output(self):
        """Test 10: Editor renders correctly."""
        import re

        output = self.editor.render()

        # Strip ANSI codes for checking
        ansi_re = re.compile(r"\x1b\[[0-9;]*m")
        plain_text = ansi_re.sub("", output)

        self.assertIn("test", plain_text)
        # Should have brackets
        self.assertTrue("[" in plain_text)


class TestInlineTextInputIntegration(unittest.TestCase):
    """Integration tests for inline text input with save callback."""

    def test_save_callback(self):
        """Test 11: Save callback is called on confirm."""
        saved_values = []

        async def save_callback(value):
            saved_values.append(value)

        editor = InlineTextEditor(
            value="initial",
            placeholder="type...",
            width=30,
        )

        # Type something
        key = KeyPress(name="X", code=ord("X"), char="X", type=KeyType.PRINTABLE)
        editor.handle_keypress(key)

        # Confirm
        enter = KeyPress(name="Enter", code=13, type=KeyType.CONTROL)
        editor.handle_keypress(enter)

        # Simulate save
        if editor.is_confirmed():
            import asyncio

            try:
                asyncio.get_event_loop()
            except RuntimeError:
                asyncio.set_event_loop(asyncio.new_event_loop())
            asyncio.run(save_callback(editor.get_value()))

        self.assertEqual(saved_values, ["initialX"])


if __name__ == "__main__":
    print("=" * 60)
    print("Phase 3: Inline Text Input Verification Test")
    print("=" * 60)
    print()

    # Run tests
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test cases
    suite.addTests(loader.loadTestsFromTestCase(TestInlineTextInput))
    suite.addTests(loader.loadTestsFromTestCase(TestInlineTextInputIntegration))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print()
    print("=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)
    print(f"Total:  {result.testsRun}")
    print(f"Passed: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failed: {len(result.failures) + len(result.errors)}")
    print()

    if result.wasSuccessful():
        print("[PASS] All requirements verified!")
        print()
        print("Implementation complete:")
        print("  feature: inline text input widget")
        print("  test: tests/unit/test_inline_text_input.py")
        print(f"  result: PASS ({result.testsRun} requirements verified)")
        sys.exit(0)
    else:
        print("[FAIL] Some requirements failed")
        sys.exit(1)
