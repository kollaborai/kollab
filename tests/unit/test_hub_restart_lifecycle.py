"""Hub self-restart lifecycle tests."""

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
