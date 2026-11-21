"""Git-only scope dispatch must use the built-in read-only executor."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from kollabor_agent.tool_executor import ToolExecutor


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def test_plugin_cannot_replace_git_only_read_only_dispatch(tmp_path):
    executor = ToolExecutor(
        mcp_integration=None,
        event_bus=None,
        terminal_timeout=30,
        mcp_timeout=30,
    )
    executor.workspace = tmp_path
    executor.event_bus = SimpleNamespace(
        emit_with_hooks=AsyncMock(return_value=None)
    )
    executor.sandbox_gate.check = Mock(return_value=None)
    executor.set_bundle_scope(["git"])
    executor.shell_executor.run_argv = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            stdout="",
            stderr="",
            exit_code=0,
            error="",
            execution_time=0.01,
        )
    )
    plugin_handler = AsyncMock()
    executor.register_plugin_handler("terminal_read", plugin_handler)

    result = _run(
        executor.execute_tool(
            {
                "id": "git-status",
                "type": "terminal",
                "command": "git status --short",
            }
        )
    )

    assert result.success, result.error
    plugin_handler.assert_not_awaited()
    executor.shell_executor.run_argv.assert_awaited_once()
