"""Regression tests for terminal-spawn guards in agent tool execution."""

import base64
from unittest.mock import MagicMock

import pytest

from kollabor_agent.tool_executor import ToolExecutor, _is_blocked_kollab_spawn


@pytest.mark.parametrize(
    "command",
    [
        "kollab --hub stop all",
        "kollab --hub=kill all",
        "env HOME=/tmp/alternate-home kollab --hub stop all",
        "env -u KOLLAB_TOOL_SHELL -u KOLLAB_AGENT_PID HOME=/tmp/alternate-home kollab --hub stop all",
        "nohup env HOME=/tmp/alternate-home kollab --detached",
        "exec -a alias kollab --agent coder",
        "setsid -f kollab --detached",
        "time -f '%e' kollab --agent coder",
        "stdbuf -o L kollab --agent coder",
        "xargs -n 1 kollab --agent coder",
        "env -S 'HOME=/tmp/alternate-home kollab --hub stop all'",
        "bash -lc 'kollab --detached'",
        "echo $(kollab --hub stop all)",
        "python3.14 main.py --agent coder",
        "python -m kollabor_cli_main --hub stop all",
        "sh -c 'env HOME=/tmp/alternate-home kollab --agent coder'",
        "python -c \"import subprocess; subprocess.Popen(['kollab', '--hub', 'stop', 'all'])\"",
        "python -c \"import os; os.popen('kollab --agent coder')\"",
    ],
)
@pytest.mark.asyncio
async def test_tool_executor_blocks_direct_and_wrapped_kollab_launches(command):
    executor = ToolExecutor(MagicMock(), MagicMock())

    result = await executor._execute_terminal_command(
        {"type": "terminal", "id": "spawn", "command": command}
    )

    assert not result.success
    assert "spawning agents via terminal" in result.error


@pytest.mark.asyncio
async def test_tool_executor_does_not_block_shell_text_that_mentions_kollab():
    executor = ToolExecutor(MagicMock(), MagicMock())

    result = await executor._execute_terminal_command(
        {
            "type": "terminal",
            "id": "echo",
            "command": "echo 'kollab --agent'",
        }
    )

    assert "spawning agents via terminal" not in result.error


@pytest.mark.asyncio
async def test_tool_executor_blocks_background_kollab_processes():
    executor = ToolExecutor(MagicMock(), MagicMock())

    result = await executor._execute_terminal_command(
        {
            "type": "terminal",
            "id": "background",
            "command": "kollab --hub status",
            "background": True,
        }
    )

    assert not result.success
    assert "spawning agents via terminal" in result.error


@pytest.mark.parametrize(
    "command",
    [
        'powershell.exe -Command "kollab --agent coder"',
        'powershell.exe -NoProfile -Command "kollab --agent coder"',
        'powershell.exe -NoLogo -NoProfile -Command "kollab --agent coder"',
        'pwsh.exe -NoProfile -Command "kollab --agent coder"',
        'PowerShell.EXE -NoP -c "kollab --agent coder"',
        'cmd.exe /c "kollab --agent coder"',
        'cmd.exe /k "kollab --agent coder"',
        'cmd.exe /d /s /c "kollab --agent coder"',
        'cmd.exe /c powershell.exe -NoProfile -Command "kollab --agent coder"',
        'unknown-shell.exe -Command "kollab --agent coder"',
        'unknown-shell.exe /c "kollab --agent coder"',
    ],
)
def test_tool_executor_spawn_guard_blocks_windows_shell_wrappers(command):
    # Call the pure guard directly so the test never executes a Windows shell.
    assert _is_blocked_kollab_spawn(command)


def test_tool_executor_spawn_guard_blocks_encoded_powershell_command():
    encoded = base64.b64encode("kollab --agent coder".encode("utf-16le")).decode(
        "ascii"
    )

    assert _is_blocked_kollab_spawn(
        f"powershell.exe -NoProfile -EncodedCommand {encoded}"
    )


def test_tool_executor_spawn_guard_blocks_encoded_command_alias_and_malformed():
    encoded = base64.b64encode("kollab --agent coder".encode("utf-16le")).decode(
        "ascii"
    )

    for command in (
        f"pwsh.exe -enc {encoded}",
        f"unknown-shell.exe -EncodedCommand {encoded}",
        "powershell.exe -EncodedCommand not-base64",
    ):
        assert _is_blocked_kollab_spawn(command)


@pytest.mark.parametrize(
    "command",
    [
        "powershell.exe -NoProfile -Command \"Write-Output 'kollab --agent'\"",
        'cmd.exe /c echo "kollab --agent"',
    ],
)
def test_tool_executor_spawn_guard_allows_windows_shell_text_mentions(command):
    assert not _is_blocked_kollab_spawn(command)
