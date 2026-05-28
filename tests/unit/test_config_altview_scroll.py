"""Regression tests for ConfigAltView long-list selection visibility."""

import re
from unittest.mock import Mock

import pytest

from kollabor_tui.key_parser import KeyPress
from plugins.altview.config_altview import ConfigAltView

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


class _Widget:
    def __init__(self, index: int) -> None:
        self.index = index
        self.focused = False
        self.config = {}
        self.config_path = f"plugins.test.widget_{index}"

    def set_focus(self, value: bool) -> None:
        self.focused = value

    def get_label(self) -> str:
        return f"Widget {self.index}"

    def render_modern(self, width: int, position: str):
        marker = ">" if self.focused else " "
        return [f"{marker} W{self.index:02d}"]


def _key(name: str, char: str | None = None) -> KeyPress:
    return KeyPress(name=name, code=char or name, char=char)


async def _render_config_view(view: ConfigAltView, width: int = 100, height: int = 20):
    renderer = Mock()
    renderer.get_terminal_size.return_value = (width, height)
    writes = []
    renderer.write_at.side_effect = lambda *args: writes.append(args)
    view._renderer = renderer

    await view.render_frame(0.0)

    return [ANSI_RE.sub("", str(args[2])) for args in writes]


def _view_with_widgets(count: int = 27) -> ConfigAltView:
    view = ConfigAltView()
    view._sections = ["Hub"]
    view._section_widgets = [[_Widget(i) for i in range(count)]]
    view._section_widgets[0][0].set_focus(True)
    return view


@pytest.mark.asyncio
async def test_config_altview_scrolls_selected_widget_into_view():
    view = _view_with_widgets()

    for _ in range(20):
        await view.handle_input(_key("ArrowDown"))

    lines = await _render_config_view(view)

    assert view._scroll_offset > 0
    assert any("> W20" in line for line in lines)
    assert not any("> W00" in line for line in lines)


@pytest.mark.asyncio
async def test_config_altview_scroll_indicator_uses_visible_window_after_clamp():
    view = _view_with_widgets()
    view._sel_widget = 20
    view._section_widgets[0][0].set_focus(False)
    view._section_widgets[0][20].set_focus(True)

    lines = await _render_config_view(view)

    indicators = [line for line in lines if "/27]" in line]
    assert indicators
    match = re.search(r"\[(\d+)-(\d+)/27\]", indicators[0])
    assert match
    start, end = int(match.group(1)), int(match.group(2))
    assert start <= 21 <= end
