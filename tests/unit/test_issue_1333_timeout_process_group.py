"""Timeout cleanup still reaches a process group after its shell exits."""

import asyncio
import os
import signal
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from kollabor_agent.shell_executor import ShellExecutor


def test_kill_unix_process_group_uses_session_leader_pid_after_exit():
    executor = ShellExecutor()
    process = AsyncMock()
    process.pid = 12345
    executor._current_process = process

    with patch("kollabor_agent.shell_executor.os.getpgid", side_effect=ProcessLookupError):
        with patch("kollabor_agent.shell_executor.os.killpg") as killpg:
            asyncio.run(executor._kill_unix_process_group())

    killpg.assert_any_call(process.pid, 15)


@pytest.mark.skipif(os.name == "nt", reason="Unix process groups are required")
def test_timeout_kills_background_child_that_ignores_sigterm(tmp_path: Path):
    pidfile = tmp_path / "child.pid"
    result = asyncio.run(
        ShellExecutor().run(
            f"sh -c 'trap \"\" TERM; exec sleep 30' & echo $! > {pidfile}; echo hi",
            timeout=1,
        )
    )
    child_pid = int(pidfile.read_text())
    try:
        deadline = time.monotonic() + 4
        state = "missing"
        while time.monotonic() < deadline:
            check = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(child_pid)],
                capture_output=True,
                text=True,
            )
            state = check.stdout.strip()
            if not state or state.startswith("Z"):
                break
            time.sleep(0.05)
        assert result.timed_out
        assert not state or state.startswith("Z"), f"background child still running: {state}"
    finally:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
