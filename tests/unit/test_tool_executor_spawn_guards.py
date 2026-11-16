"""Regression tests for terminal-spawn guards in agent tool execution."""

import base64
from unittest.mock import MagicMock

import pytest

from kollabor_agent.tool_executor import ToolExecutor, _is_blocked_kollab_spawn


@pytest.mark.parametrize(
    "command",
    [
        "kollab --hub stop all",
        "kollab --hub=kill all",
        "env HOME=/tmp/alternate-home kollab --hub stop all",
        "env -u KOLLAB_TOOL_SHELL -u KOLLAB_AGENT_PID HOME=/tmp/alternate-home kollab --hub stop all",
        "nohup env HOME=/tmp/alternate-home kollab --detached",
        "exec -a alias kollab --agent coder",
        "setsid -f kollab --detached",
        "time -f '%e' kollab --agent coder",
        "stdbuf -o L kollab --agent coder",
        "xargs -n 1 kollab --agent coder",
        "env -S 'HOME=/tmp/alternate-home kollab --hub stop all'",
        "bash -lc 'kollab --detached'",
        "echo $(kollab --hub stop all)",
        "python3.14 main.py --agent coder",
        "python -m kollabor_cli_main --hub stop all",
        "sh -c 'env HOME=/tmp/alternate-home kollab --agent coder'",
        "python -c \"import subprocess; subprocess.Popen(['kollab', '--hub', 'stop', 'all'])\"",
        "python -c \"import os; os.popen('kollab --agent coder')\"",
    ],
)
@pytest.mark.asyncio
async def test_tool_executor_blocks_direct_and_wrapped_kollab_launches(command):
    executor = ToolExecutor(MagicMock(), MagicMock())

    result = await executor._execute_terminal_command(
        {"type": "terminal", "id": "spawn", "command": command}
    )

    assert not result.success
    assert "spawning agents via terminal" in result.error


@pytest.mark.asyncio
async def test_tool_executor_does_not_block_shell_text_that_mentions_kollab():
    executor = ToolExecutor(MagicMock(), MagicMock())

    result = await executor._execute_terminal_command(
        {
            "type": "terminal",
            "id": "echo",
            "command": "echo 'kollab --agent'",
        }
    )

    assert "spawning agents via terminal" not in result.error


@pytest.mark.asyncio
async def test_tool_executor_blocks_background_kollab_processes():
    executor = ToolExecutor(MagicMock(), MagicMock())

    result = await executor._execute_terminal_command(
        {
            "type": "terminal",
            "id": "background",
            "command": "kollab --hub status",
            "background": True,
        }
    )

    assert not result.success
    assert "spawning agents via terminal" in result.error


@pytest.mark.parametrize(
    "command",
    [
        'powershell.exe -Command "kollab --agent coder"',
        'powershell.exe -NoProfile -Command "kollab --agent coder"',
        'powershell.exe -NoLogo -NoProfile -Command "kollab --agent coder"',
        'pwsh.exe -NoProfile -Command "kollab --agent coder"',
        'PowerShell.EXE -NoP -c "kollab --agent coder"',
        'cmd.exe /c "kollab --agent coder"',
        'cmd.exe /k "kollab --agent coder"',
        'cmd.exe /d /s /c "kollab --agent coder"',
        'cmd.exe /c powershell.exe -NoProfile -Command "kollab --agent coder"',
        'unknown-shell.exe -Command "kollab --agent coder"',
        'unknown-shell.exe /c "kollab --agent coder"',
    ],
)
def test_tool_executor_spawn_guard_blocks_windows_shell_wrappers(command):
    # Call the pure guard directly so the test never executes a Windows shell.
    assert _is_blocked_kollab_spawn(command)


def test_tool_executor_spawn_guard_blocks_encoded_powershell_command():
    encoded = base64.b64encode("kollab --agent coder".encode("utf-16le")).decode(
        "ascii"
    )

    assert _is_blocked_kollab_spawn(
        f"powershell.exe -NoProfile -EncodedCommand {encoded}"
    )


def test_tool_executor_spawn_guard_blocks_encoded_command_alias_and_malformed():
    encoded = base64.b64encode("kollab --agent coder".encode("utf-16le")).decode(
        "ascii"
    )

    for command in (
        f"pwsh.exe -enc {encoded}",
        f"unknown-shell.exe -EncodedCommand {encoded}",
        "powershell.exe -EncodedCommand not-base64",
    ):
        assert _is_blocked_kollab_spawn(command)


@pytest.mark.parametrize(
    "command",
    [
        "powershell.exe -NoProfile -Command \"Write-Output 'kollab --agent'\"",
        'cmd.exe /c echo "kollab --agent"',
    ],
)
def test_tool_executor_spawn_guard_allows_windows_shell_text_mentions(command):
    assert not _is_blocked_kollab_spawn(command)


@pytest.mark.parametrize(
    "command",
    [
        "kollab --hub msg lapis -d is the flag",
        "kollab --hub msg lapis -a is the flag",
        "kollab --hub stop lapis all",
        "kollab --hub stop --reason r all",
    ],
)
def test_tool_executor_spawn_guard_allows_hub_free_text(command):
    assert not _is_blocked_kollab_spawn(command)


@pytest.mark.parametrize(
    "command",
    [
        "grep -E 'passed|failed' test.log",
        "grep -e passed test.log",
        "grep -ec passed test.log",
        "grep -enc passed test.log",
        "rg -e passed tests",
        "git grep -e passed",
        "test -e README.md",
        "printf x | grep -E x",
        "git status | grep -E main",
        "pytest tests/unit | grep -E passed",
    ],
)
def test_spawn_guard_allows_unrelated_short_e_options(command):
    # These flags belong to their own executables, not to PowerShell.
    assert not _is_blocked_kollab_spawn(command)


@pytest.mark.asyncio
async def test_terminal_executor_allows_benign_filtered_command():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    executor = ToolExecutor(MagicMock(), MagicMock())
    executor.shell_executor.run = AsyncMock(
        return_value=SimpleNamespace(
            error="", success=True, stdout="ready\n", stderr="", exit_code=0
        )
    )
    command = "printf ready | grep -E ready"

    result = await executor._execute_terminal_command(
        {"type": "terminal", "id": "benign-filter", "command": command}
    )

    assert result.success
    assert result.output == "ready\n"
    executor.shell_executor.run.assert_awaited_once()


def test_generic_encoded_short_alias_blocks_actual_launch():
    encoded = base64.b64encode("kollab --agent coder".encode("utf-16le")).decode(
        "ascii"
    )
    for option in ("-e", "-ec", "-enc"):
        assert _is_blocked_kollab_spawn(f"unknown-shell.exe {option} {encoded}")


@pytest.mark.parametrize("tool", ["grep", "rg", "git grep"])
def test_search_command_can_match_a_guarded_launch_as_text(tool):
    assert not _is_blocked_kollab_spawn(f"{tool} -c 'kollab --agent coder'")


@pytest.mark.parametrize("tool", ["grep", "rg", "git grep"])
def test_search_command_can_match_encoded_launch_as_text(tool):
    encoded = base64.b64encode("kollab --agent coder".encode("utf-16le")).decode(
        "ascii"
    )
    assert not _is_blocked_kollab_spawn(f"{tool} -e {encoded}")


@pytest.mark.parametrize("tool", ["grep", "rg", "git grep"])
def test_search_command_does_not_hide_command_substitutions(tool):
    assert _is_blocked_kollab_spawn(f"{tool} -e x $(kollab --agent coder)")


@pytest.mark.parametrize("option", ["-e", "-ec", "-enc", "-EncodedCommand"])
def test_unknown_shell_malformed_encoded_command_stays_blocked(option):
    assert _is_blocked_kollab_spawn(f"unknown-shell.exe {option} not-base64")


@pytest.mark.parametrize("option", ["-e", "-ec", "-enc", "-EncodedCommand"])
def test_unknown_shell_encoded_input_remains_conservatively_blocked(option):
    encoded = base64.b64encode("Write-Output hello".encode("utf-16le")).decode("ascii")
    assert _is_blocked_kollab_spawn(f"unknown-shell.exe {option} {encoded}")


@pytest.mark.parametrize(
    "command",
    [
        "node -e \"console.log('hello')\"",
        "sed -E 's/foo/bar/' sample.txt",
        "tar -E archive.tar",
    ],
)
def test_unrelated_programs_with_short_e_are_allowed(command):
    assert not _is_blocked_kollab_spawn(command)


@pytest.mark.parametrize(
    "script",
    [
        "require('child_process').exec('kollab --agent coder')",
        "require('child_process').spawn('kollab', ['--agent', 'coder'])",
        "require('child_process').exec('kollab --hub stop all')",
    ],
)
def test_inline_node_launches_remain_blocked(script):
    assert _is_blocked_kollab_spawn(f'node -e "{script}"')


@pytest.mark.parametrize(
    "command",
    [
        "rg -e pattern --pre='kollab --agent coder' sample",
        "git grep -e pattern --open-files-in-pager='kollab --agent coder'",
        "git grep -e pattern -O'kollab --agent coder'",
    ],
)
def test_search_exceptions_do_not_exempt_executable_options(command):
    assert _is_blocked_kollab_spawn(command)


@pytest.mark.parametrize(
    "command",
    [
        "rg -e 'kollab --agent coder' --pre=cat sample",
        "git grep -e 'kollab --agent coder' --open-files-in-pager=cat",
    ],
)
def test_safe_search_execution_option_does_not_interpret_pattern_as_command(command):
    assert not _is_blocked_kollab_spawn(command)


@pytest.mark.parametrize(
    "command",
    [
        "git -C /tmp/project/kollab fetch origin main 2>&1",
        "git -C /tmp/project/kollab status 2>&1 | tail -5",
        "git -C /tmp/project/kollab status >&2",
        "git -C /tmp/project/kollab status <&0",
        "git -C /tmp/project/kollab fetch origin main 2>&1 &",
    ],
)
def test_fd_redirects_and_background_git_are_not_agent_launches(command):
    assert not _is_blocked_kollab_spawn(command)


@pytest.mark.parametrize(
    "command",
    [
        "kollab --agent coder 2>&1",
        "kollab --hub stop all 2>&1",
        "kollab --hub status 2>&1 &",
        "echo safe 2>&1 && kollab --agent coder",
        "unknown-shell.exe -C 'kollab --agent coder'",
    ],
)
def test_redirects_preserve_real_agent_guard(command):
    assert _is_blocked_kollab_spawn(command)
