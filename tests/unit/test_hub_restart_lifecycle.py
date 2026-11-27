"""Hub self-restart lifecycle tests."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from plugins.hub.plugin import HubPlugin


@pytest.mark.asyncio
async def test_self_restart_runs_exec_after_non_exiting_shutdown():
    plugin = object.__new__(HubPlugin)
    plugin._self_restart_cmd = ["/usr/bin/python", "main.py"]
    plugin.shutdown = AsyncMock()

    with patch("plugins.hub.plugin.os.execvp") as execvp:
        await plugin._perform_self_restart()

    plugin.shutdown.assert_awaited_once_with(exit_process=False)
    execvp.assert_called_once_with("/usr/bin/python", ["/usr/bin/python", "main.py"])


@pytest.mark.asyncio
async def test_graceful_restart_cancels_watchdog_and_execs_once():
    plugin = object.__new__(HubPlugin)
    plugin._self_restart_cmd = ["/usr/bin/python", "main.py"]
    plugin._self_restart_exec_started = False
    plugin.shutdown = AsyncMock()
    plugin._self_restart_watchdog_task = asyncio.create_task(asyncio.sleep(60))

    with patch("plugins.hub.plugin.os.execvp") as execvp:
        await plugin._perform_self_restart()
        await asyncio.sleep(0)

    assert plugin._self_restart_watchdog_task.cancelled()
    execvp.assert_called_once_with("/usr/bin/python", ["/usr/bin/python", "main.py"])


@pytest.mark.asyncio
async def test_restart_exec_guard_allows_only_one_claim():
    plugin = object.__new__(HubPlugin)
    plugin._self_restart_exec_started = False

    assert plugin._claim_self_restart_exec() is True
    assert plugin._claim_self_restart_exec() is False


@pytest.mark.asyncio
async def test_restart_watchdog_exits_nonzero_when_cleanup_is_unconfirmed():
    plugin = object.__new__(HubPlugin)
    plugin._self_restart_exec_started = False
    plugin._shutdown_complete = False
    exec_argv = ["/usr/bin/python", "main.py"]
    exit_codes = []

    with (
        patch("plugins.hub.plugin.os.execvp") as execvp,
        patch("plugins.hub.plugin.os._exit", side_effect=exit_codes.append),
        patch("plugins.hub.plugin.asyncio.sleep", new=AsyncMock()),
    ):
        await plugin._self_restart_watchdog(exec_argv, "koordinator")

    assert plugin._self_restart_exec_started is False
    assert exit_codes == [1]
    execvp.assert_not_called()


@pytest.mark.asyncio
async def test_restart_watchdog_execs_only_after_cleanup_is_confirmed():
    plugin = object.__new__(HubPlugin)
    plugin._self_restart_exec_started = False
    plugin._shutdown_complete = True
    exec_argv = ["/usr/bin/python", "main.py"]

    with (
        patch("plugins.hub.plugin.os.execvp") as execvp,
        patch("plugins.hub.plugin.os._exit") as exit_process,
        patch("plugins.hub.plugin.asyncio.sleep", new=AsyncMock()),
    ):
        await plugin._self_restart_watchdog(exec_argv, "koordinator")

    assert plugin._self_restart_exec_started is True
    execvp.assert_called_once_with(exec_argv[0], exec_argv)
    exit_process.assert_not_called()
