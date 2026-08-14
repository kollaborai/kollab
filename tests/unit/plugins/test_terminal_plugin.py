"""Focused terminal timeout lifecycle tests."""

import asyncio
from unittest.mock import Mock

import pytest

from plugins.terminal_plugin import TerminalSession, TmuxPlugin


@pytest.mark.asyncio
async def test_shutdown_cancels_and_clears_timeout_tasks():
    plugin = TmuxPlugin()
    task = asyncio.create_task(asyncio.sleep(60))
    plugin._timeout_tasks["session"] = task

    await plugin.shutdown()

    assert task.cancelled()
    assert not plugin._timeout_tasks


@pytest.mark.asyncio
async def test_killing_session_cancels_its_timeout_task():
    plugin = TmuxPlugin()
    process = Mock()
    process.poll.return_value = 0
    plugin.sessions["session"] = TerminalSession(
        name="session", command="true", proc=process
    )
    task = asyncio.create_task(asyncio.sleep(60))
    plugin._timeout_tasks["session"] = task

    result = await plugin.kill_background_session("session")

    assert result["success"]
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert "session" not in plugin.sessions
    assert "session" not in plugin._timeout_tasks


@pytest.mark.asyncio
async def test_timeout_task_failure_is_observed(caplog):
    plugin = TmuxPlugin()

    async def fail():
        raise RuntimeError("timer boom")

    task = asyncio.create_task(fail())
    plugin._timeout_tasks["session"] = task

    await asyncio.sleep(0)
    with caplog.at_level("ERROR", logger="plugins.terminal_plugin"):
        plugin._on_timeout_task_done("session", task)

    assert "session" not in plugin._timeout_tasks
    assert "Terminal timeout task failed for session 'session'" in caplog.text
