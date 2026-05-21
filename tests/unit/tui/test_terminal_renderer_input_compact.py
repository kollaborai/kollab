"""Tests for compact terminal input rendering colors."""

from kollabor_events.models import CommandMode
from kollabor_tui.design_system import T, set_theme
from kollabor_tui.terminal_renderer import TerminalRenderer


class _InputHandler:
    def __init__(self, command_mode):
        self.command_mode = command_mode


def test_default_input_uses_graphite_fill_for_border_too():
    """Normal input uses one dark fill so the frame does not stripe."""
    original_theme = T().name
    set_theme("dark")
    try:
        renderer = TerminalRenderer()

        assert renderer._get_input_base_color(is_shell=False) == T().input_bg[0]
        assert renderer._get_input_border_color(is_shell=False) == T().input_bg[0]
        assert renderer._get_input_shimmer_color(is_shell=False) == T().input_bg[0]
    finally:
        set_theme(original_theme)


def test_slash_menu_input_keeps_solid_graphite_fill():
    """Opening the slash menu should not turn only the borders orange."""
    original_theme = T().name
    set_theme("dark")
    try:
        renderer = TerminalRenderer()
        renderer.input_handler = _InputHandler(CommandMode.MENU_POPUP)

        assert renderer._get_input_base_color(is_shell=False) == T().input_bg[0]
        assert renderer._get_input_border_color(is_shell=False) == T().input_bg[0]
        assert renderer._get_input_shimmer_color(is_shell=False) == T().input_bg[0]
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


def test_default_input_cursor_uses_full_block_when_idle():
    """The visible cursor is a full block, not a half-height/side marker."""
    renderer = TerminalRenderer()
    renderer._last_activity = 0

    assert renderer._get_cursor_char(simple_mode=False) == "█"
