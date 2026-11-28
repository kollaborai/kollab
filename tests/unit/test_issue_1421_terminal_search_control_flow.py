"""Regression coverage for find searches nested in shell control flow."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_agent.tool_executor import ToolExecutor


@pytest.mark.asyncio
async def test_terminal_find_in_if_block_does_not_search_outside_workspace():
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
                "id": "terminal-find-if-block",
                "command": (
                    f"if true; then find {outside} -maxdepth 1 "
                    "-name protected-marker.txt -print; fi"
                ),
                "cwd": str(workspace),
            }
        )

        assert not result.success
        assert str(marker) not in result.output
