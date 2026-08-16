"""Regression tests for message-coordinator render scheduling."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kollabor_tui.message_coordinator import MessageDisplayCoordinator


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
