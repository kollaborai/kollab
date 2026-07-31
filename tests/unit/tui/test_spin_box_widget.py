"""SpinBoxWidget construction regressions.

The constructor used to call self.get_value() -- the subclass override that
reads self.current_value before __init__ sets it -- so EVERY construction
raised AttributeError. It also crashed on float("") when the config system
had no stored value. These tests pin the fixed behavior.
"""

import unittest

from kollabor_tui.widgets.spin_box import SpinBoxWidget


class TestSpinBoxConstruction(unittest.TestCase):
    def test_constructs_without_stored_value_defaults_to_midpoint(self):
        widget = SpinBoxWidget(
            {"label": "Max Tokens", "min_value": 0, "max_value": 100},
            "test.spinbox",
            None,
        )
        self.assertEqual(widget.get_value(), 50)

    def test_constructs_with_prefilled_value(self):
        widget = SpinBoxWidget(
            {"label": "Timeout", "min_value": 0, "max_value": 10, "value": 7},
            "test.spinbox",
            None,
        )
        self.assertEqual(widget.get_value(), 7)

    def test_non_numeric_stored_value_falls_back_to_midpoint(self):
        widget = SpinBoxWidget(
            {"label": "Temp", "min_value": 0, "max_value": 2, "value": ""},
            "test.spinbox",
            None,
        )
        self.assertEqual(widget.current_value, 1.0)

    def test_renders_after_construction(self):
        widget = SpinBoxWidget(
            {"label": "Max Tokens", "min_value": 0, "max_value": 100},
            "test.spinbox",
            None,
        )
        lines = widget.render()
        self.assertTrue(lines)


if __name__ == "__main__":
    unittest.main()
