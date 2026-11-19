"""Lifecycle regression tests for terminal execution safety."""

from unittest.mock import MagicMock

import pytest

from kollabor_agent.tool_executor import ToolExecutor


@pytest.mark.asyncio
async def test_spawn_guard_does_not_block_unrelated_command_text():
    """Only commands that invoke kollab/main.py are blocked by the guard."""
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: True if key == "tools.block_kollab_terminal_spawn" else default
    executor = ToolExecutor(MagicMock(), MagicMock(), config=config)

    result = await executor._execute_terminal_command(
        {"type": "terminal", "id": "echo", "command": "echo 'kollab --agent'"}
    )

    assert "tools.block_kollab_terminal_spawn" not in result.error
