"""Application background-task lifecycle tests."""

import asyncio
import logging

import pytest

from kollabor.application import TerminalLLMChat


@pytest.mark.asyncio
async def test_fire_and_forget_failure_is_retrieved_and_logged(caplog):
    app = object.__new__(TerminalLLMChat)
    app._background_tasks = []

    async def failing_task():
        raise RuntimeError("startup exploded")

    with caplog.at_level(logging.ERROR, logger="kollabor.application"):
        task = app.create_background_task(failing_task(), "deferred_startup")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert task.done()
    assert task not in app._background_tasks
    assert "Background task failed: deferred_startup" in caplog.text
    assert "RuntimeError: startup exploded" in caplog.text
