"""DropdownWidget option labels: the text people read, never what is saved.

A dropdown whose values are ids ("marketing-crew") can name them for people
("Marketing Crew") with ``option_labels``; a value without a label shows as
itself, and what the widget holds and saves stays the raw option.
"""

import re
import unittest

from kollabor_tui.widgets.dropdown import DropdownWidget

LABELS = {"all": "All", "koordinator": "Koordinator", "none": "None"}


def _widget(value="koordinator", labels=LABELS, **extra):
    config = {
        "label": "Wake on Agent Arrival",
        "options": ["all", "koordinator", "none", "custom-peer"],
        "current_value": value,
        **extra,
    }
    if labels is not None:
        config["option_labels"] = labels
    return DropdownWidget(config, "plugins.hub.arrival_wake", None)


def _plain(lines):
    return re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(lines))


class TestDropdownOptionLabels(unittest.TestCase):
    def test_the_collapsed_line_shows_the_label(self):
        text = _plain(_widget().render())
        self.assertIn("Wake on Agent Arrival: [Koordinator ▼]", text)
        self.assertNotIn("koordinator", text)

    def test_the_expanded_list_shows_labels_and_marks_the_current_option(self):
        widget = _widget()
        widget.set_focus(True)
        widget._expanded = True
        text = _plain(widget.render())
        self.assertIn("> Koordinator", text)
        for label in ("All", "None"):
            self.assertIn(label, text)

    def test_modern_render_shows_labels_in_the_header_and_the_list(self):
        widget = _widget(value="none")
        widget._expanded = True
        text = _plain(widget.render_modern(width=60))
        self.assertIn("Wake on Agent Arrival: None", text)
        self.assertIn("● None", text)
        self.assertIn("○ Koordinator", text)

    def test_an_option_without_a_label_shows_as_itself(self):
        widget = _widget(value="custom-peer")
        self.assertIn("[custom-peer ▼]", _plain(widget.render()))
        widget._expanded = True
        self.assertIn("● custom-peer", _plain(widget.render_modern(width=60)))

    def test_a_dropdown_with_no_labels_renders_as_before(self):
        widget = _widget(value="all", labels=None)
        self.assertIn("[all ▼]", _plain(widget.render()))

    def test_cycling_and_validation_use_the_raw_values(self):
        widget = _widget(value="all")
        widget._cycle_option(1)
        self.assertEqual(widget.get_pending_value(), "koordinator")
        self.assertTrue(widget.is_valid_value("none"))
        self.assertFalse(widget.is_valid_value("None"))
        self.assertIn("[Koordinator ▼]", _plain(widget.render()))


if __name__ == "__main__":
    unittest.main()
