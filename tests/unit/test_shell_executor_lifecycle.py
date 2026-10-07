"""Lifecycle tests for ShellExecutor cancellation and cleanup."""

import asyncio
import os

import pytest

from kollabor_agent.shell_executor import ShellExecutor


@pytest.mark.asyncio
async def test_outer_task_cancellation_propagates_after_process_cleanup():
    """Task cancellation must propagate instead of becoming a normal result."""
    executor = ShellExecutor()
    task = asyncio.create_task(executor.run("sleep 10", timeout=30))

    for _ in range(100):
        if executor._current_process is not None:
            break
        await asyncio.sleep(0.01)

    process = executor._current_process
    assert process is not None
    pid = process.pid

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert executor._current_process is None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(("command", "code"), [("true", 0), ("false", 1), ("exit 3", 3)])
async def test_exit_code_is_the_process_return_code(command, code):
    """A successful command reports exit 0, not 1."""
    result = await ShellExecutor().run(command, timeout=10)

    assert result.exit_code == code
    assert result.success is (code == 0)
