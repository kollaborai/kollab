"""Tests for simple fallback message rendering."""

from kollabor_tui.simple_renderer import SimpleRenderer


def test_simple_agent_message_starts_with_separator_line():
    rendered = SimpleRenderer().agent_message("lapis -> koordinator")

    assert rendered.splitlines()[0] == ""
    assert rendered.splitlines()[1] == " ◆ lapis -> koordinator"
