"""Regression tests for terminal-spawn guards in agent tool execution."""

from unittest.mock import MagicMock

import pytest

from kollabor_agent.tool_executor import ToolExecutor


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
