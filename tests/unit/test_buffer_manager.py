"""Tests for atomic buffer edits used by token-aware input features."""

from kollabor_tui.buffer_manager import BufferManager


def test_delete_range_removes_atomic_content_and_repositions_cursor():
    buffer = BufferManager()
    for char in "left [image1] right":
        assert buffer.insert_char(char)

    buffer.move_to_end()
    assert buffer.delete_range(5, 13)

    assert buffer.content == "left  right"
    assert buffer.cursor_position == len(buffer.content)


def test_replace_content_clamps_cursor_and_does_not_add_history():
    buffer = BufferManager()
    buffer.add_to_history("previous")

    buffer.replace_content("rewritten", cursor_position=100)

    assert buffer.content == "rewritten"
    assert buffer.cursor_position == len("rewritten")
    assert buffer.navigate_history("up")
    assert buffer.content == "previous"
