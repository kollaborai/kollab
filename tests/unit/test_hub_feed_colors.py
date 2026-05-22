"""Tests for hub feed agent color readability."""

from kollabor_tui.color_contrast import contrast_ratio
from kollabor_tui.design_system import T, set_theme
from plugins.hub.feed import _color


def test_feed_lifts_dark_gem_colors_for_black_terminals():
    original = T().name
    set_theme("dark")
    try:
        color = _color("sapphire")

        assert color != (15, 82, 186)
        assert contrast_ratio(color, (0, 0, 0)) >= 6.5
    finally:
        set_theme(original)
