"""Focused lifecycle tests for KeyPressHandler background tasks."""

import asyncio
from unittest.mock import patch

import pytest

from kollabor_tui.input.key_press_handler import KeyPressHandler


def make_handler() -> KeyPressHandler:
    """Build a handler with dependency-only test doubles."""
    return KeyPressHandler(
        buffer_manager=object(),
        key_parser=object(),
        event_bus=object(),
        error_handler=object(),
        display_controller=object(),
        paste_processor=object(),
        renderer=object(),
    )


@pytest.mark.asyncio
async def test_background_task_failure_is_observed():
    handler = make_handler()

    async def fail():
        raise RuntimeError("hook failed")

    with patch(
        "kollabor_tui.input.key_press_handler.logger.exception"
    ) as log_exception:
        handler._create_background_task(fail())
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert not handler._background_tasks
    log_exception.assert_called_once_with("Key press background task failed")


@pytest.mark.asyncio
async def test_cleanup_cancels_pending_background_tasks():
    handler = make_handler()
    started = asyncio.Event()
    release = asyncio.Event()

    async def wait_for_release():
        started.set()
        await release.wait()

    handler._create_background_task(wait_for_release())
    await started.wait()
    assert len(handler._background_tasks) == 1

    await handler.cleanup()

    assert not handler._background_tasks
