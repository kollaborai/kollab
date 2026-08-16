"""Focused lifecycle tests for InputHandler."""

from unittest.mock import AsyncMock, Mock

import pytest

from kollabor_tui.input_handler import InputHandler


@pytest.mark.asyncio
async def test_stop_cleans_key_press_handler_before_loop_manager():
    handler = InputHandler.__new__(InputHandler)
    handler.running = True

    calls: list[str] = []

    key_press_handler = Mock()
    key_press_handler.cleanup = AsyncMock(
        side_effect=lambda: calls.append("keypress")
    )
    loop_manager = Mock()
    loop_manager.stop = AsyncMock(side_effect=lambda: calls.append("loop"))

    handler._key_press_handler = key_press_handler
    handler._input_loop_manager = loop_manager

    await handler.stop()

    assert handler.running is False
    assert calls == ["keypress", "loop"]
    key_press_handler.cleanup.assert_awaited_once_with()
    loop_manager.stop.assert_awaited_once_with()
