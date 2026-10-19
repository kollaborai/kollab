"""Regression tests for message-coordinator render scheduling."""

import asyncio
import logging
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from kollabor_tui.message_coordinator import MessageDisplayCoordinator
from kollabor_tui.terminal_renderer import TerminalRenderer


class _TerminalState:
    def write_raw(self, _text):
        pass


class _Terminal:
    pipe_mode = False
    writing_messages = False
    input_line_written = False
    last_line_count = 0

    def __init__(self):
        self.terminal_state = _TerminalState()

    def clear_active_area(self):
        pass

    def invalidate_render_cache(self):
        pass


@pytest.mark.asyncio
async def test_display_raw_text_requests_attached_render_loop():
    terminal = _Terminal()
    coordinator = MessageDisplayCoordinator(terminal, renderer=SimpleNamespace())
    render_loop = SimpleNamespace(request_render=Mock())
    coordinator.set_render_loop(render_loop)

    coordinator.display_raw_text("hello")

    render_loop.request_render.assert_called_once_with()


def test_display_queued_messages_requests_attached_render_loop():
    terminal = _Terminal()
    coordinator = MessageDisplayCoordinator(terminal, renderer=SimpleNamespace())
    coordinator._display_single_message = Mock()
    render_loop = SimpleNamespace(request_render=Mock())
    coordinator.set_render_loop(render_loop)
    coordinator.queue_message("system", "hello")

    coordinator.display_queued_messages()

    render_loop.request_render.assert_called_once_with()


@pytest.mark.asyncio
async def test_display_raw_text_fallback_observes_render_failure(caplog):
    caplog.set_level(logging.ERROR, logger="kollabor_tui.message_coordinator")
    terminal = _Terminal()

    async def render_active_area():
        raise RuntimeError("render failed")

    terminal.render_active_area = render_active_area
    coordinator = MessageDisplayCoordinator(terminal, renderer=SimpleNamespace())

    coordinator.display_raw_text("hello")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert "Render task failed after raw text display" in caplog.text


class _Screen:
    """The rows a terminal shows for the cursor escapes the renderer writes."""

    _TOKEN = re.compile(r"\x1b\[(\??)([\d;]*)([A-Za-z])|\r|\n|[^\x1b\r\n]+")

    def __init__(self, rows):
        self.rows = list(rows)
        self.row, self.col = len(self.rows) - 1, 0

    def write(self, text):
        for match in self._TOKEN.finditer(text):
            token, private, params, command = match.group(0, 1, 2, 3)
            while len(self.rows) <= self.row:
                self.rows.append("")
            if token == "\r":
                self.col = 0
            elif token == "\n":
                self.row += 1
            elif command == "A" and not private:
                self.row = max(0, self.row - int(params or 1))
            elif command == "B" and not private:
                self.row += int(params or 1)
            elif command == "K":
                self.rows[self.row] = ""
            elif command == "J":
                self.rows[self.row] = self.rows[self.row][: self.col]
                del self.rows[self.row + 1 :]
            elif not command:
                line = self.rows[self.row].ljust(self.col)
                self.rows[self.row] = (
                    line[: self.col] + token + line[self.col + len(token) :]
                )
                self.col += len(token)


@pytest.mark.asyncio
async def test_force_ready_keeps_the_active_area_drawn_before_it():
    """A turn that ends after the render loop drew its active area leaves one box.

    2026-10-19: a voice turn awaits its spoken reply after the reply shows, so
    the render loop drew the Working active area before the queue processor's
    force_ready(). force_ready forgot it, and the next render cleared one row
    and drew a second input box under the first, every voice turn.
    """
    renderer = TerminalRenderer()
    renderer.terminal_state = MagicMock()
    screen = _Screen(["▌ hello", "֎ pong: hello", ""])
    renderer.terminal_state.write_raw = screen.write
    working = [
        "",
        "❯",
        "",
        "cwd",
        "Working mic speaking",
        "1 msg",
        "default",
        "+ mcp:1",
    ]
    ready = [*working[:4], "Ready mic listening", *working[5:]]

    await renderer._render_lines(working)  # drawn while voice speaks the reply
    renderer.message_coordinator.force_ready()  # the queue processor's finally
    await renderer._render_lines(ready)

    assert screen.rows == ["▌ hello", "֎ pong: hello", *ready]
