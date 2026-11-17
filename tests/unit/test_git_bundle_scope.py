"""Tests for Git-only agent bundle scope enforcement."""

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


def _make_git_scoped_executor(workspace):
    executor = ToolExecutor(
        mcp_integration=None,
        event_bus=None,
        terminal_timeout=30,
        mcp_timeout=30,
    )
    executor.workspace = workspace
    executor.sandbox_gate.check = Mock(return_value=None)
    executor.set_bundle_scope(["file-read", "file-grep", "git"])
    executor.shell_executor.run_argv = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            stdout="## main\n",
            stderr="",
            exit_code=0,
            error="",
            execution_time=0.01,
        )
    )
    return executor


def test_xml_terminal_git_status_uses_git_only_scope_through_read_only_argv(
    tmp_path,
):
    executor = _make_git_scoped_executor(tmp_path)

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
    executor.shell_executor.run_argv.assert_awaited_once()
    argv = executor.shell_executor.run_argv.await_args.args[0]
    assert argv[0].endswith("/git")
    assert argv[1:4] == ("-c", "core.fsmonitor=false", "status")
    assert argv[4:] == ("--short",)
    assert executor.shell_executor.run_argv.await_args.kwargs["cwd"] == tmp_path


def test_legacy_git_tool_alias_uses_the_same_read_only_scope(tmp_path):
    executor = _make_git_scoped_executor(tmp_path)

    result = _run(
        executor.execute_tool(
            {
                "id": "git-status",
                "type": "git",
                "command": "git status --short",
            }
        )
    )

    assert result.success, result.error
    executor.shell_executor.run_argv.assert_awaited_once()


def test_git_only_scope_rejects_shellouts_helpers_and_non_read_commands(tmp_path):
    executor = _make_git_scoped_executor(tmp_path)
    git_config = tmp_path / ".git" / "config"
    git_config.parent.mkdir()
    git_config.write_text(
        "[alias]\n\tscopeprobe = !touch marker\n",
        encoding="utf-8",
    )
    commands = (
        "git scopeprobe",
        "git -c alias.scopeprobe=!touch marker scopeprobe",
        "git -c diff.external='/usr/bin/touch /tmp/pwned' diff",
        "git -c core.pager='sh -c touch marker' log -1",
        "git credential fill",
        "git bisect run sh -c 'touch marker'",
        "git --paginate log -1",
        "git -p log -1",
        "git log --paginate -1",
        "git status --paginate",
        "git commit -m review",
        'git fetch "ext::touch marker"',
        "git status --short && touch marker",
        "printf hello",
    )

    for index, command in enumerate(commands):
        result = _run(
            executor.execute_tool(
                {
                    "id": f"blocked-{index}",
                    "type": "terminal",
                    "command": command,
                }
            )
        )

        assert not result.success, command
        assert result.metadata.get("scope_denied"), command

    executor.shell_executor.run_argv.assert_not_awaited()
