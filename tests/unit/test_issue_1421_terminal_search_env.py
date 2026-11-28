"""Regression coverage for assignment-prefixed terminal find searches."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_agent.tool_executor import ToolExecutor


@pytest.mark.asyncio
@pytest.mark.parametrize("assignment", ["SEARCH=1", 'OUTSIDE="{outside}";'])
async def test_terminal_find_with_shell_assignment_cannot_search_outside(assignment):
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        outside = root / "outside"
        workspace.mkdir()
        outside.mkdir()
        marker = outside / "protected-marker.txt"
        marker.write_text("outside\n")
        if assignment == "SEARCH=1":
            command = f"{assignment} find {outside} -maxdepth 1 -name protected-marker.txt -print"
        else:
            command = f'{assignment.format(outside=outside)} find "$OUTSIDE" -maxdepth 1 -name protected-marker.txt -print'

        executor = ToolExecutor(
            mcp_integration=MagicMock(),
            event_bus=MagicMock(emit_with_hooks=AsyncMock(return_value={})),
            terminal_timeout=10,
            workspace=workspace,
        )
        result = await executor.execute_tool(
            {
                "type": "terminal",
                "id": "terminal-find-assignment",
                "command": command,
                "cwd": str(workspace),
            }
        )

        assert not result.success
        assert str(marker) not in result.output
