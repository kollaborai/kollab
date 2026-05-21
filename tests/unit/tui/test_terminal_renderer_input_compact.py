"""Tests for compact terminal input rendering colors."""

from kollabor_tui.design_system import T, set_theme
from kollabor_tui.terminal_renderer import TerminalRenderer


def test_default_input_uses_graphite_fill_and_subtle_border():
    """Normal input uses a dark fill with a subtle graphite border."""
    original_theme = T().name
    set_theme("dark")
    try:
        renderer = TerminalRenderer()

        assert renderer._get_input_base_color(is_shell=False) == T().input_bg[0]
        assert renderer._get_input_border_color(is_shell=False) == T().secondary[0]
    finally:
        set_theme(original_theme)


def test_shell_input_keeps_error_border():
    """Shell commands keep the warning/error border treatment."""
    original_theme = T().name
    set_theme("dark")
    try:
        renderer = TerminalRenderer()

        assert renderer._get_input_border_color(is_shell=True) == T().error[0]
    finally:
        set_theme(original_theme)
