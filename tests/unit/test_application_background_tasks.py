"""Application background-task lifecycle tests."""

import asyncio
import logging
from unittest.mock import AsyncMock

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


@pytest.mark.asyncio
async def test_cleanup_retains_task_that_outlives_cancellation_timeout(monkeypatch):
    app = object.__new__(TerminalLLMChat)
    app._background_tasks = []
    app.running = True
    app._startup_complete = True
    app.shutdown = AsyncMock()

    release = asyncio.Event()

    async def cancellation_resistant_task():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    task = app.create_background_task(
        cancellation_resistant_task(), "cancellation_resistant"
    )
    await asyncio.sleep(0)

    async def immediate_timeout(tasks, timeout):
        assert timeout == 5.0
        return set(), set(tasks)

    monkeypatch.setattr(asyncio, "wait", immediate_timeout)

    await app.cleanup()

    assert task in app._background_tasks
    assert not task.done()
    app.shutdown.assert_awaited_once()

    release.set()
    await task
    await asyncio.sleep(0)

    assert task not in app._background_tasks


@pytest.mark.asyncio
async def test_cleanup_is_idempotent_for_repeated_calls():
    app = object.__new__(TerminalLLMChat)
    app._background_tasks = []
    app.running = True
    app._startup_complete = True
    app.shutdown = AsyncMock()

    await app.cleanup()
    await app.cleanup()

    app.shutdown.assert_awaited_once()
