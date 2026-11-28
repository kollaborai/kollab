"""Regression coverage for find searches in shell command substitutions."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_agent.tool_executor import ToolExecutor


@pytest.mark.asyncio
async def test_terminal_find_in_command_substitution_cannot_search_outside():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        outside = root / "outside"
        workspace.mkdir()
        outside.mkdir()
        marker = outside / "protected-marker.txt"
        marker.write_text("outside\n")

        executor = ToolExecutor(
            mcp_integration=MagicMock(),
            event_bus=MagicMock(emit_with_hooks=AsyncMock(return_value={})),
            terminal_timeout=10,
            workspace=workspace,
        )
        result = await executor.execute_tool(
            {
                "type": "terminal",
                "id": "terminal-find-substitution",
                "command": (
                    f"echo $(find {outside} -maxdepth 1 "
                    "-name protected-marker.txt -print)"
                ),
                "cwd": str(workspace),
            }
        )

        assert not result.success
        assert str(marker) not in result.output
