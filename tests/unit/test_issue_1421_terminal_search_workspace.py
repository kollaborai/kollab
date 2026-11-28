"""Regression coverage for terminal searches escaping the active workspace."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_agent.tool_executor import ToolExecutor


@pytest.mark.asyncio
async def test_terminal_find_does_not_search_outside_active_workspace():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        outside = root / "outside"
        workspace.mkdir()
        outside.mkdir()
        marker = outside / "protected-marker.txt"
        marker.write_text("outside\n")

        event_bus = MagicMock()
        event_bus.emit_with_hooks = AsyncMock(return_value={})
        executor = ToolExecutor(
            mcp_integration=MagicMock(),
            event_bus=event_bus,
            terminal_timeout=10,
            workspace=workspace,
        )

        result = await executor.execute_tool(
            {
                "type": "terminal",
                "id": "terminal-find-outside",
                "command": f"find {outside} -maxdepth 1 -name protected-marker.txt -print",
                "cwd": str(workspace),
            }
        )

        assert not result.success
        assert str(marker) not in result.output
