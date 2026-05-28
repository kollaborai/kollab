"""Tests for slash command menu color consistency."""

import re
from types import SimpleNamespace

from kollabor_tui.design_system import (
    COLOR_TRUECOLOR,
    T,
    get_color_mode,
    set_color_mode,
    set_theme,
)
from kollabor_tui.menu_renderer import CommandMenuRenderer

BG_RE = re.compile(r"48;2;(\d+);(\d+);(\d+)")
ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def test_selected_command_row_uses_one_solid_background():
    """Selected slash command rows should not be segmented orange/grey."""
    original_theme = T().name
    original_color_mode = get_color_mode()
    set_theme("dark")
    set_color_mode(COLOR_TRUECOLOR)
    try:
        menu = CommandMenuRenderer(SimpleNamespace(_app_config=None))
        menu._get_menu_width = lambda: 72  # type: ignore[method-assign]

        line = menu._format_command_line(
            {
                "name": "compact",
                "description": "Show compaction profile and trigger info",
                "aliases": [],
                "_is_selected": True,
            },
            "system",
        )

        backgrounds = set(BG_RE.findall(line))
        user_tag = tuple(str(part) for part in T().user_tag)

        assert len(backgrounds) == 1
        assert user_tag not in backgrounds
        assert "/compact" in _strip_ansi(line)
        assert "Show compaction profile" in _strip_ansi(line)
    finally:
        set_color_mode(original_color_mode)
        set_theme(original_theme)


def test_unselected_command_row_fills_background_for_readability():
    """Unselected command descriptions should not float over terminal wallpaper."""
    original_theme = T().name
    original_color_mode = get_color_mode()
    set_theme("dark")
    set_color_mode(COLOR_TRUECOLOR)
    try:
        menu = CommandMenuRenderer(SimpleNamespace(_app_config=None))
        menu._get_menu_width = lambda: 72  # type: ignore[method-assign]

        line = menu._format_command_line(
            {
                "name": "mcp",
                "description": "Open the MCP manager",
                "aliases": [],
                "_is_selected": False,
            },
            "system",
        )

        assert len(BG_RE.findall(line)) >= 72
        assert "/mcp" in _strip_ansi(line)
        assert "Open the MCP manager" in _strip_ansi(line)
    finally:
        set_color_mode(original_color_mode)
        set_theme(original_theme)


def test_unselected_subcommand_row_fills_background_for_readability():
    """Subcommand text should stay legible when it is not selected."""
    original_theme = T().name
    original_color_mode = get_color_mode()
    set_theme("dark")
    set_color_mode(COLOR_TRUECOLOR)
    try:
        menu = CommandMenuRenderer(SimpleNamespace(_app_config=None))
        menu._get_menu_width = lambda: 72  # type: ignore[method-assign]

        line = menu._format_subcommand_item(
            {
                "subcommand_name": "show",
                "subcommand_args": "",
                "subcommand_desc": "Show MCP status panel",
            },
            is_selected=False,
        )

        assert len(BG_RE.findall(line)) >= 72
        assert "show" in _strip_ansi(line)
        assert "Show MCP status panel" in _strip_ansi(line)
    finally:
        set_color_mode(original_color_mode)
        set_theme(original_theme)


def test_filtered_command_menu_keeps_search_rank_over_category_sort():
    """Typing /mod should keep /mode above /model even though model is system."""
    menu = CommandMenuRenderer(SimpleNamespace(_app_config=None))

    menu.show_command_menu(
        [
            {"name": "mode", "description": "", "aliases": [], "category": "ui"},
            {"name": "model", "description": "", "aliases": [], "category": "system"},
        ]
    )
    menu.filter_commands(
        [
            {"name": "mode", "description": "", "aliases": [], "category": "ui"},
            {"name": "model", "description": "", "aliases": [], "category": "system"},
        ],
        "mod",
    )

    assert [item["name"] for item in menu.menu_items[:2]] == ["mode", "model"]
