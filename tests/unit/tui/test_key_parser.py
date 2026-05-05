"""Tests for kollabor_tui.key_parser module.

Tests the public API of the KeyParser class and KeyPress dataclass
including parsing basic characters, control keys, and escape sequences.
"""

import sys
import unittest
from pathlib import Path

# Add packages/kollabor-tui to path for imports
sys.path.insert(
    0,
    str(
        Path(__file__).parent.parent.parent.parent / "packages" / "kollabor-tui" / "src"
    ),
)

from kollabor_tui.key_parser import KeyParser, KeyPress, KeyType


class TestKeyPress(unittest.TestCase):
    """Test cases for KeyPress dataclass."""

    def test_keypress_has_required_fields(self):
        """Test that KeyPress has required fields."""
        kp = KeyPress(name="a", code=97, char="a")

        self.assertEqual(kp.name, "a")
        self.assertEqual(kp.code, 97)
        self.assertEqual(kp.char, "a")
        self.assertEqual(kp.type, KeyType.PRINTABLE)

    def test_keypress_default_modifiers(self):
        """Test that KeyPress initializes with default modifiers."""
        kp = KeyPress(name="a", code=97)

        expected_modifiers = {
            "ctrl": False,
            "alt": False,
            "shift": False,
            "cmd": False,
        }
        self.assertEqual(kp.modifiers, expected_modifiers)

    def test_keypress_custom_type(self):
        """Test KeyPress with custom type."""
        kp = KeyPress(name="Enter", code=13, type=KeyType.CONTROL)
        self.assertEqual(kp.type, KeyType.CONTROL)

    def test_keypress_ctrl_modifier(self):
        """Test KeyPress with ctrl modifier."""
        kp = KeyPress(
            name="Ctrl+C",
            code=3,
            type=KeyType.CONTROL,
            modifiers={"ctrl": True, "alt": False, "shift": False, "cmd": False},
        )
        self.assertTrue(kp.modifiers["ctrl"])


class TestKeyParser(unittest.TestCase):
    """Test cases for KeyParser class."""

    def test_parser_initialization(self):
        """Test that KeyParser initializes correctly."""
        parser = KeyParser()
        self.assertFalse(parser._in_escape_sequence)
        self.assertEqual(parser._escape_buffer, "")

    def test_parse_basic_char_a(self):
        """Test parsing basic character 'a'."""
        parser = KeyParser()
        result = parser.parse_char("a")

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "a")
        self.assertEqual(result.code, 97)
        self.assertEqual(result.char, "a")
        self.assertEqual(result.type, KeyType.PRINTABLE)

    def test_parse_basic_char_z(self):
        """Test parsing basic character 'z'."""
        parser = KeyParser()
        result = parser.parse_char("z")

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "z")
        self.assertEqual(result.code, 122)
        self.assertEqual(result.char, "z")

    def test_parse_enter_key(self):
        """Test parsing Enter key (carriage return)."""
        parser = KeyParser()
        result = parser.parse_char("\r")

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Enter")
        self.assertEqual(result.code, 13)
        self.assertEqual(result.type, KeyType.CONTROL)

    def test_parse_ctrl_c(self):
        """Test parsing Ctrl+C."""
        parser = KeyParser()
        result = parser.parse_char("\x03")  # Ctrl+C

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Ctrl+C")
        self.assertEqual(result.code, 3)
        self.assertEqual(result.type, KeyType.CONTROL)
        self.assertTrue(result.modifiers["ctrl"])

    def test_parse_tab(self):
        """Test parsing Tab key."""
        parser = KeyParser()
        result = parser.parse_char("\t")

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Tab")
        self.assertEqual(result.code, 9)
        self.assertEqual(result.type, KeyType.CONTROL)

    def test_parse_backspace(self):
        """Test parsing Backspace key."""
        parser = KeyParser()
        result = parser.parse_char("\x7f")  # DEL (backspace)

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Backspace")
        self.assertEqual(result.code, 127)
        self.assertEqual(result.type, KeyType.CONTROL)

    def test_parse_escape_sequence_starts(self):
        """Test that escape key starts sequence parsing."""
        parser = KeyParser()
        result = parser.parse_char("\x1b")  # ESC

        # ESC starts a sequence, returns None until sequence completes
        self.assertIsNone(result)
        self.assertTrue(parser._in_escape_sequence)

    def test_parse_arrow_up(self):
        """Test parsing ArrowUp escape sequence."""
        parser = KeyParser()
        parser.parse_char("\x1b")  # ESC
        result = parser.parse_char("[")

        self.assertIsNone(result)  # Not complete yet

        result = parser.parse_char("A")
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "ArrowUp")
        self.assertEqual(result.type, KeyType.EXTENDED)

    def test_parse_arrow_down(self):
        """Test parsing ArrowDown escape sequence."""
        parser = KeyParser()
        parser.parse_char("\x1b")  # ESC
        parser.parse_char("[")
        result = parser.parse_char("B")

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "ArrowDown")

    def test_parse_arrow_left(self):
        """Test parsing ArrowLeft escape sequence."""
        parser = KeyParser()
        parser.parse_char("\x1b")  # ESC
        parser.parse_char("[")
        result = parser.parse_char("D")

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "ArrowLeft")

    def test_parse_arrow_right(self):
        """Test parsing ArrowRight escape sequence."""
        parser = KeyParser()
        parser.parse_char("\x1b")  # ESC
        parser.parse_char("[")
        result = parser.parse_char("C")

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "ArrowRight")

    def test_parse_home(self):
        """Test parsing Home key."""
        parser = KeyParser()
        parser.parse_char("\x1b")  # ESC
        parser.parse_char("[")
        result = parser.parse_char("H")

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Home")

    def test_parse_end(self):
        """Test parsing End key."""
        parser = KeyParser()
        parser.parse_char("\x1b")  # ESC
        parser.parse_char("[")
        result = parser.parse_char("F")

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "End")

    def test_parse_delete(self):
        """Test parsing Delete key."""
        parser = KeyParser()
        parser.parse_char("\x1b")  # ESC
        parser.parse_char("[")
        parser.parse_char("3")
        result = parser.parse_char("~")

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Delete")

    def test_parse_page_up(self):
        """Test parsing PageUp key."""
        parser = KeyParser()
        parser.parse_char("\x1b")  # ESC
        parser.parse_char("[")
        parser.parse_char("5")
        result = parser.parse_char("~")

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "PageUp")

    def test_parse_page_down(self):
        """Test parsing PageDown key."""
        parser = KeyParser()
        parser.parse_char("\x1b")  # ESC
        parser.parse_char("[")
        parser.parse_char("6")
        result = parser.parse_char("~")

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "PageDown")

    def test_is_printable_char(self):
        """Test is_printable_char method."""
        parser = KeyParser()

        # Printable character
        kp = KeyPress(name="a", code=97, char="a", type=KeyType.PRINTABLE)
        self.assertTrue(parser.is_printable_char(kp))

        # Control key
        kp_ctrl = KeyPress(name="Enter", code=13, type=KeyType.CONTROL)
        self.assertFalse(parser.is_printable_char(kp_ctrl))

        # No char (should return False)
        kp_no_char = KeyPress(name="test", code=0, type=KeyType.PRINTABLE)
        self.assertFalse(parser.is_printable_char(kp_no_char))

    def test_is_control_key(self):
        """Test is_control_key method."""
        parser = KeyParser()

        # Control key match
        kp = KeyPress(name="Enter", code=13, type=KeyType.CONTROL)
        self.assertTrue(parser.is_control_key(kp, "Enter"))

        # Control key mismatch
        self.assertFalse(parser.is_control_key(kp, "Tab"))

        # Wrong type
        kp_printable = KeyPress(name="a", code=97, type=KeyType.PRINTABLE)
        self.assertFalse(parser.is_control_key(kp_printable, "a"))

    def test_check_for_standalone_escape(self):
        """Test check_for_standalone_escape method."""
        parser = KeyParser()

        # No escape sequence started
        result = parser.check_for_standalone_escape()
        self.assertIsNone(result)

        # Escape sequence started with empty buffer
        parser._in_escape_sequence = True
        parser._escape_buffer = ""
        result = parser.check_for_standalone_escape()

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Escape")
        self.assertFalse(parser._in_escape_sequence)

    def test_ctrl_arrow_modifiers(self):
        """Test that Ctrl+Arrow sequences have correct modifiers."""
        parser = KeyParser()
        parser.parse_char("\x1b")  # ESC
        parser.parse_char("[")
        parser.parse_char("1")
        parser.parse_char(";")
        parser.parse_char("5")
        result = parser.parse_char("A")  # Ctrl+ArrowUp

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Ctrl+ArrowUp")
        self.assertTrue(result.modifiers["ctrl"])


if __name__ == "__main__":
    unittest.main()
