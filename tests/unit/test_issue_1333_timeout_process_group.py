"""Timeout cleanup still reaches a process group after its shell exits."""

import asyncio
from unittest.mock import AsyncMock, patch

from kollabor_agent.shell_executor import ShellExecutor


def test_kill_unix_process_group_uses_session_leader_pid_after_exit():
    executor = ShellExecutor()
    process = AsyncMock()
    process.pid = 12345
    executor._current_process = process

    with patch("kollabor_agent.shell_executor.os.getpgid", side_effect=ProcessLookupError):
        with patch("kollabor_agent.shell_executor.os.killpg") as killpg:
            asyncio.run(executor._kill_unix_process_group())

    killpg.assert_called_once_with(process.pid, 15)
