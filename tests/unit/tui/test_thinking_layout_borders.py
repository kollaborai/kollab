"""Tests for compact thinking/waiting rendering."""

from kollabor_tui.render_layout import ThinkingAnimationManager
from kollabor_tui.status.utils import strip_ansi


def test_modern_thinking_renders_without_block_borders():
    """The waiting/thinking indicator should not add block border rows."""
    manager = ThinkingAnimationManager()
    manager.start_thinking("Waiting...")

    lines = manager.get_display_lines_modern(width=64)
    plain = [strip_ansi(line) for line in lines]

    assert len(plain) == 3
    assert plain[0] == ""
    assert "Waiting..." in plain[1]
    assert plain[2] == ""
    assert "38;" in lines[1]
    assert "48;" not in lines[1]
    assert not {"▄", "▀"} & set("".join(plain))
