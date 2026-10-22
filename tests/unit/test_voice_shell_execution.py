"""Voice terminal plans execute argv directly, never through a shell."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from kollabor_agent.shell_executor import ShellExecutor


@pytest.mark.asyncio
async def test_run_argv_uses_exec_without_invoking_shell(tmp_path):
    process = SimpleNamespace(
        returncode=0,
        stdin=None,
        stdout=None,
        stderr=None,
        communicate=AsyncMock(return_value=(b"clean\n", b"")),
    )
    executor = ShellExecutor()

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as exec_call,
        patch(
            "asyncio.create_subprocess_shell",
            new=AsyncMock(side_effect=AssertionError("voice argv must not use a shell")),
        ) as shell_call,
    ):
        result = await executor.run_argv(
            ["git", "status", "--short"],
            cwd=tmp_path,
        )

    assert result.success is True
    assert result.stdout == "clean\n"
    assert exec_call.await_args.args[:3] == ("git", "status", "--short")
    shell_call.assert_not_awaited()
