"""Regression coverage for find searches hidden by shell wrappers."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_agent.tool_executor import ToolExecutor


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wrapper",
    [
        "eval 'find {outside} -maxdepth 1 -name protected-marker.txt -print'",
        "sh -c 'find {outside} -maxdepth 1 -name protected-marker.txt -print'",
        'FIND=find; "$FIND" {outside} -maxdepth 1 -name protected-marker.txt -print',
    ],
)
async def test_terminal_find_wrapper_cannot_search_outside_workspace(wrapper):
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        workspace, outside = root / "workspace", root / "outside"
        workspace.mkdir()
        outside.mkdir()
        marker = outside / "protected-marker.txt"
        marker.write_text("outside\n")
        command = wrapper.format(outside=outside)
        executor = ToolExecutor(
            mcp_integration=MagicMock(),
            event_bus=MagicMock(emit_with_hooks=AsyncMock(return_value={})),
            terminal_timeout=10,
            workspace=workspace,
        )

        result = await executor.execute_tool(
            {
                "type": "terminal",
                "id": "terminal-find-wrapper",
                "command": command,
                "cwd": str(workspace),
            }
        )

        assert not result.success, f"wrapper ran outside find: {result.output}"
        assert str(marker) not in result.output
