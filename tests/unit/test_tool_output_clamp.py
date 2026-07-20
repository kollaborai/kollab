"""Regression tests for tool-output line clamping (no raw-terminal overflow).

Long tool-output lines (e.g. a grep hit with a long path) used to be written
with only a 4-space indent and no width clamp, so the terminal hard-wrapped
them at the raw edge -- breaking mid-word and dumping the tail at column 0.
`_clamp_display_line` clips each preview line to the terminal width instead.
"""

from types import SimpleNamespace
from unittest.mock import patch

import kollabor_tui.tool_display as td


def test_long_line_is_clamped_with_ellipsis():
    with patch.object(td, "get_terminal_width", return_value=40):
        out = td._clamp_display_line("x" * 100)
    # budget = max(20, 40 - 5) = 35
    assert len(out) == 35
    assert out.endswith("…")
    assert out.startswith("x")


def test_short_line_unchanged():
    with patch.object(td, "get_terminal_width", return_value=120):
        assert td._clamp_display_line("short line") == "short line"


def test_tabs_expanded_before_measuring():
    with patch.object(td, "get_terminal_width", return_value=120):
        assert "\t" not in td._clamp_display_line("a\tb")


def test_format_tool_output_never_exceeds_width():
    result = SimpleNamespace(tool_type="shell", output="z" * 300, metadata=None)
    with patch.object(td, "get_terminal_width", return_value=50):
        lines = td.format_tool_output(result)
    assert lines, "expected at least one formatted line"
    for ln in lines:
        # 4-space indent + clamped content, must fit within the terminal width
        assert len(ln) <= 50, f"line overflowed width: {len(ln)} > 50"
    # the single long line got clipped, so the ellipsis must be present
    assert any("…" in ln for ln in lines)


def test_narrow_terminal_has_a_floor():
    # A pathologically narrow width must not clip everything to nothing.
    with patch.object(td, "get_terminal_width", return_value=3):
        out = td._clamp_display_line("y" * 100)
    assert len(out) == 20  # max(20, 3 - 5) floor
