"""Tests for per-line diffing in TerminalRenderer._render_lines()."""

from unittest.mock import MagicMock

import pytest

from kollabor_tui.terminal_renderer import TerminalRenderer


def _make_renderer():
    """Create a renderer with a mock terminal_state that captures raw writes."""
    renderer = TerminalRenderer()
    # Capture all raw writes.
    renderer._raw_output: list[str] = []
    renderer.terminal_state = MagicMock()
    renderer.terminal_state.write_raw = renderer._raw_output.append
    renderer.terminal_state.get_size.return_value = (80, 24)
    renderer.terminal_state.is_resize_in_progress.return_value = False
    # Disable config so _should_enable_render_cache uses _render_cache_enabled.
    renderer._app_config = None
    return renderer


def _flush(renderer):
    """Join all captured raw writes into a single string."""
    return "".join(renderer._raw_output)


@pytest.mark.asyncio
async def test_first_render_does_full_clear_and_write():
    """First render should use the full rewrite path (no previous content)."""
    r = _make_renderer()
    lines = ["line A", "line B", "line C"]

    await r._render_lines(lines)

    out = _flush(r)
    # Should contain clear codes and all three lines.
    assert "line A" in out
    assert "line B" in out
    assert "line C" in out
    # Should have cleared (first render clears current line).
    assert "\033[2K" in out
    # Cursor hidden.
    assert "\033[?25l" in out
    # Cache should be populated.
    assert r._last_render_content == lines
    assert r.last_line_count == 3
    assert r.input_line_written is True


@pytest.mark.asyncio
async def test_unchanged_lines_skip_render():
    """Second render with identical lines should produce no output."""
    r = _make_renderer()
    lines = ["line A", "line B", "line C"]

    # First render.
    await r._render_lines(lines)
    r._raw_output.clear()

    # Second render — identical.
    await r._render_lines(lines)

    assert _flush(r) == ""


@pytest.mark.asyncio
async def test_single_line_change_only_writes_that_line():
    """Changing one line should only rewrite that line, not all lines."""
    r = _make_renderer()
    lines = ["line A", "line B", "line C"]

    # First render.
    await r._render_lines(lines)
    r._raw_output.clear()

    # Change only line 1.
    new_lines = ["line A", "line B CHANGED", "line C"]
    await r._render_lines(new_lines)

    out = _flush(r)
    # Should contain the changed line.
    assert "line B CHANGED" in out
    # Should NOT contain unchanged lines (they're not rewritten).
    assert "line A" not in out
    assert "line C" not in out
    # Should have cursor-up movement to reach line 1.
    # bottom = 2, target = line 1, up = 1
    assert "\033[1A" in out
    # Should move back down.
    assert "\033[1B" in out


@pytest.mark.asyncio
async def test_line_count_change_triggers_full_rewrite():
    """Adding or removing lines should trigger full rewrite path."""
    r = _make_renderer()
    lines = ["line A", "line B"]

    await r._render_lines(lines)
    r._raw_output.clear()

    # Add a line.
    new_lines = ["line A", "line B", "line C"]
    await r._render_lines(new_lines)

    out = _flush(r)
    # Full rewrite: all lines present.
    assert "line A" in out
    assert "line B" in out
    assert "line C" in out
    # Should use newline separators (full rewrite pattern).
    assert "\n" in out


@pytest.mark.asyncio
async def test_size_changed_triggers_full_rewrite():
    """Terminal resize should trigger full rewrite with aggressive clear."""
    r = _make_renderer()
    lines = ["line A", "line B"]

    await r._render_lines(lines)
    r._raw_output.clear()

    # Resize-triggered render with same content.
    await r._render_lines(lines, size_changed=True)

    out = _flush(r)
    # Full rewrite: both lines present.
    assert "line A" in out
    assert "line B" in out
    # Resize clear pattern.
    assert "\033[J" in out


@pytest.mark.asyncio
async def test_invalidate_cache_forces_full_rewrite():
    """After invalidate_render_cache(), next render is full rewrite."""
    r = _make_renderer()
    lines = ["line A", "line B"]

    await r._render_lines(lines)
    r._raw_output.clear()

    # Invalidate and re-render same content.
    r.invalidate_render_cache()
    await r._render_lines(lines)

    out = _flush(r)
    # Full rewrite: prev_content is empty after invalidate.
    assert "line A" in out
    assert "line B" in out


@pytest.mark.asyncio
async def test_all_lines_change_writes_all():
    """When every line changes, all lines should be rewritten via diff path."""
    r = _make_renderer()
    lines = ["A", "B", "C"]

    await r._render_lines(lines)
    r._raw_output.clear()

    new_lines = ["X", "Y", "Z"]
    await r._render_lines(new_lines)

    out = _flush(r)
    assert "X" in out
    assert "Y" in out
    assert "Z" in out
    # Should NOT use newline separators (diff path uses cursor moves).
    assert "\n" not in out


@pytest.mark.asyncio
async def test_multiple_changes_batched_in_one_flush():
    """Multiple changed lines should be written in a single flush."""
    r = _make_renderer()
    lines = ["A", "B", "C", "D"]

    await r._render_lines(lines)
    r._raw_output.clear()

    # Change lines 0 and 2.
    new_lines = ["A*", "B", "C*", "D"]
    await r._render_lines(new_lines)

    out = _flush(r)
    assert "A*" in out
    assert "C*" in out
    # Unchanged lines B and D should not appear as literal content.
    # Strip ANSI escape codes before checking (cursor-down \033[3B contains "B").
    import re
    plain = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', out)
    plain = plain.replace('\r', '')
    assert "B" not in plain
    assert "D" not in plain
