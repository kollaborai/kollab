"""TextInputWidget cursor + backspace regressions.

Prefilled fields used to start with cursor_position=0: Backspace was a no-op
and typing prepended. Under tmux/screen the backspace key arrives as 0x08,
which the parser used to name "Ctrl+H" -- a dead key for every text widget.
"""

import unittest

from kollabor_tui.key_parser import KeyParser, KeyPress
from kollabor_tui.widgets.text_input import TextInputWidget


def _widget(value: str) -> TextInputWidget:
    return TextInputWidget({"label": "Name", "value": value}, "test.path", None)


def _key(name: str, char: str = "") -> KeyPress:
    return KeyPress(name=name, code=char and ord(char) or 0, char=char or None)


class TestPrefilledCursor(unittest.TestCase):
    def test_cursor_starts_at_end_of_prefilled_value(self):
        widget = _widget("gpt-5.6")
        self.assertEqual(widget.cursor_position, 7)

    def test_backspace_deletes_last_char_of_prefill(self):
        widget = _widget("gpt-5.6")
        widget.handle_input(_key("Backspace"))
        self.assertEqual(str(widget.get_pending_value()), "gpt-5.")

    def test_typing_appends_to_prefill(self):
        widget = _widget("gpt")
        widget.handle_input(_key("x", "x"))
        self.assertEqual(str(widget.get_pending_value()), "gptx")

    def test_clear_and_retype_round_trip(self):
        widget = _widget("128000")
        for _ in range(6):
            widget.handle_input(_key("Backspace"))
        for ch in "32000":
            widget.handle_input(_key(ch, ch))
        self.assertEqual(str(widget.get_pending_value()), "32000")

    def test_focus_gain_moves_cursor_to_end(self):
        widget = _widget("hello")
        widget.cursor_position = 0
        widget.set_focus(True)
        self.assertEqual(widget.cursor_position, 5)

    def test_cursor_clamped_after_external_shrink(self):
        widget = _widget("longvalue")
        widget.set_value("ab")
        widget.handle_input(_key("Backspace"))
        self.assertEqual(str(widget.get_pending_value()), "a")


class TestBackspaceByteMapping(unittest.TestCase):
    def test_0x08_maps_to_backspace(self):
        self.assertEqual(KeyParser.CONTROL_KEYS[8], "Backspace")

    def test_0x7f_maps_to_backspace(self):
        self.assertEqual(KeyParser.CONTROL_KEYS[127], "Backspace")


class TestTypeThenBackspace(unittest.TestCase):
    def test_fresh_typing_then_backspace_deletes_from_end(self):
        # Regression: Backspace decremented the cursor twice (set_value's
        # clamp + the explicit decrement), so "abc" + 2x Backspace gave "b".
        widget = _widget("")
        for ch in "abc":
            widget.handle_input(_key(ch, ch))
        widget.handle_input(_key("Backspace"))
        widget.handle_input(_key("Backspace"))
        self.assertEqual(str(widget.get_pending_value()), "a")


if __name__ == "__main__":
    unittest.main()
