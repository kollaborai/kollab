"""Tests for compact terminal input rendering colors."""

from kollabor_events.models import CommandMode
from kollabor_tui.design_system import T, set_theme
from kollabor_tui.status.utils import strip_ansi
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


def test_default_input_cursor_uses_full_block_when_idle():
    """The visible cursor is a full block, not a half-height/side marker."""
    renderer = TerminalRenderer()
    renderer._last_activity = 0

    assert renderer._get_cursor_char(simple_mode=False) == "█"


def test_modern_input_renders_without_block_borders():
    """The active input line should not add top/bottom block border rows."""
    renderer = TerminalRenderer()
    renderer._last_activity = 0

    lines: list[str] = []
    renderer._render_input_modern(lines, position="only")
    plain = [strip_ansi(line) for line in lines]

    assert len(plain) == 1
    assert plain[0].lstrip().startswith("❯")
    assert not {"▄", "▀"} & set("".join(plain))


def test_modern_input_line_has_no_background_fill():
    """The input row should not render a full-width background fill."""
    renderer = TerminalRenderer()
    renderer._last_activity = 0

    lines: list[str] = []
    renderer._render_input_modern(lines, position="only")

    line = lines[0]

    assert "38;" in line
    assert "48;" not in line


def test_active_lines_keep_one_blank_separator_between_input_and_status():
    """Removing borders still leaves one clean spacer before status widgets."""
    renderer = TerminalRenderer()
    renderer._last_activity = 0

    class _LayoutRenderer:
        def render(self):
            return ["  cwd ~/dev/kollab"]

    renderer.layout_renderer = _LayoutRenderer()

    import asyncio

    plain = [strip_ansi(line) for line in asyncio.run(renderer._build_active_lines())]

    assert len(plain) == 4
    assert plain[0] == ""
    assert plain[1].lstrip().startswith("❯")
    assert plain[2] == ""
    assert plain[3].startswith("  cwd")
    assert not {"▄", "▀"} & set("".join(plain))
