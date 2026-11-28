"""Regression coverage for terminal searches escaping the active workspace."""

import os
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


@pytest.mark.asyncio
async def test_terminal_find_rejects_parent_traversal_and_symlink_roots():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        outside = root / "outside"
        workspace.mkdir()
        outside.mkdir()
        marker = outside / "protected-marker.txt"
        marker.write_text("outside\n")
        if os.name == "nt":
            pytest.skip("directory symlinks require extra permissions on Windows")
        (workspace / "outside-link").symlink_to(outside, target_is_directory=True)

        event_bus = MagicMock()
        event_bus.emit_with_hooks = AsyncMock(return_value={})
        executor = ToolExecutor(
            mcp_integration=MagicMock(),
            event_bus=event_bus,
            terminal_timeout=10,
            workspace=workspace,
        )

        for search_root in ("../outside", "outside-link"):
            result = await executor.execute_tool(
                {
                    "type": "terminal",
                    "id": f"terminal-find-{search_root}",
                    "command": (
                        f"find {search_root} -maxdepth 1 "
                        "-name protected-marker.txt -print"
                    ),
                    "cwd": str(workspace),
                }
            )
            assert not result.success
            assert str(marker) not in result.output


@pytest.mark.asyncio
async def test_terminal_find_refuses_symlink_follow_and_allows_workspace_search():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        outside = root / "outside"
        workspace.mkdir()
        outside.mkdir()
        (outside / "protected-marker.txt").write_text("outside\n")
        (workspace / "workspace-marker.txt").write_text("inside\n")
        if os.name == "nt":
            pytest.skip("directory symlinks require extra permissions on Windows")
        (workspace / "outside-link").symlink_to(outside, target_is_directory=True)

        event_bus = MagicMock()
        event_bus.emit_with_hooks = AsyncMock(return_value={})
        executor = ToolExecutor(
            mcp_integration=MagicMock(),
            event_bus=event_bus,
            terminal_timeout=10,
            workspace=workspace,
        )

        followed = await executor.execute_tool(
            {
                "type": "terminal",
                "id": "terminal-find-follow",
                "command": "find -L . -maxdepth 2 -name protected-marker.txt -print",
                "cwd": str(workspace),
            }
        )
        inside = await executor.execute_tool(
            {
                "type": "terminal",
                "id": "terminal-find-inside",
                "command": "find . -maxdepth 1 -name workspace-marker.txt -print",
                "cwd": str(workspace),
            }
        )

        assert not followed.success
        assert "symbolic links" in followed.error.lower()
        assert inside.success
        assert "workspace-marker.txt" in inside.output
