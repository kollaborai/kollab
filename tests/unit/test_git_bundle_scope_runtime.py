"""Exercise Git-only bundle scope against a real temporary Git repository."""

import asyncio
import os
import subprocess
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


def test_git_only_xml_terminal_runs_status_in_temporary_repository(tmp_path):
    git_env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    git_env["GIT_CONFIG_NOSYSTEM"] = "1"
    git_env["GIT_CONFIG_GLOBAL"] = os.devnull
    subprocess.run(
        ["git", "init", "--quiet", str(tmp_path)],
        check=True,
        env=git_env,
    )
    (tmp_path / "probe.txt").write_text("temporary status check\n", encoding="utf-8")

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
    assert result.output.strip() == "?? probe.txt"
