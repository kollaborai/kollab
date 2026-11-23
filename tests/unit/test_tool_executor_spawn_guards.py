"""Regression tests for terminal-spawn guards in agent tool execution."""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_agent.tool_executor import ToolExecutor, _is_blocked_kollab_spawn

FLAG = "tools.block_kollab_terminal_spawn"


class _Config:
    """Minimal config stub exposing only ``get`` with dotted keys."""

    def __init__(self, **values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


def _executor(block: bool | None = None) -> ToolExecutor:
    """Executor whose shell is mocked so no Kollab command ever really runs."""
    config = None if block is None else _Config(**{FLAG: block})
    executor = ToolExecutor(MagicMock(), MagicMock(), config=config)
    executor.shell_executor.run = AsyncMock(
        return_value=SimpleNamespace(error="", success=True, stdout="synthetic result\n", stderr="", exit_code=0)
    )
    return executor


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
async def test_tool_executor_blocks_direct_and_wrapped_kollab_launches_when_opted_in(
    command,
):
    executor = _executor(block=True)

    result = await executor._execute_terminal_command({"type": "terminal", "id": "spawn", "command": command})

    assert not result.success
    assert "tools.block_kollab_terminal_spawn" in result.error
    executor.shell_executor.run.assert_not_awaited()


@pytest.mark.parametrize(
    "command",
    [
        "kollab --hub stop all",
        "nohup env HOME=/tmp/alternate-home kollab --detached",
        "bash -lc 'kollab --detached'",
        "kollab --agent coder",
        "kollab --hub status",
        "kollab --version",
        "kollab --help",
    ],
)
@pytest.mark.parametrize("config_state", [None, False])
@pytest.mark.asyncio
async def test_tool_executor_allows_kollab_commands_by_default(command, config_state):
    executor = _executor(block=config_state)

    result = await executor._execute_terminal_command({"type": "terminal", "id": "default-free", "command": command})

    assert result.success
    assert result.output == "synthetic result\n"
    executor.shell_executor.run.assert_awaited_once()


@pytest.mark.parametrize("config_state", [None, False])
@pytest.mark.parametrize("command", ["kollab --hub status", "kollab --version", "kollab --help"])
@pytest.mark.asyncio
async def test_tool_executor_allows_background_kollab_commands_by_default(command, config_state):
    executor = _executor(block=config_state)

    result = await executor._execute_terminal_command(
        {"type": "terminal", "id": "bg-free", "command": command, "background": True}
    )

    assert result.success
    executor.shell_executor.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_tool_executor_does_not_block_shell_text_that_mentions_kollab():
    executor = _executor(block=True)

    result = await executor._execute_terminal_command(
        {
            "type": "terminal",
            "id": "echo",
            "command": "echo 'kollab --agent'",
        }
    )

    assert result.success
    executor.shell_executor.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_tool_executor_blocks_background_kollab_processes_when_opted_in():
    executor = _executor(block=True)

    result = await executor._execute_terminal_command(
        {
            "type": "terminal",
            "id": "background",
            "command": "kollab --hub status",
            "background": True,
        }
    )

    assert not result.success
    assert "tools.block_kollab_terminal_spawn" in result.error
    assert "foreground" in result.error
    assert "hub_spawn" in result.error
    assert "/hub stop all" in result.error
    executor.shell_executor.run.assert_not_awaited()


@pytest.mark.parametrize("command", ["kollab --version", "kollab --help", "kollab --hub status"])
@pytest.mark.asyncio
async def test_tool_executor_allows_foreground_informational_kollab_when_opted_in(
    command,
):
    executor = _executor(block=True)

    result = await executor._execute_terminal_command({"type": "terminal", "id": "foreground-info", "command": command})

    assert result.success
    assert result.output == "synthetic result\n"
    executor.shell_executor.run.assert_awaited_once()


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
    encoded = base64.b64encode("kollab --agent coder".encode("utf-16le")).decode("ascii")

    assert _is_blocked_kollab_spawn(f"powershell.exe -NoProfile -EncodedCommand {encoded}")


def test_tool_executor_spawn_guard_blocks_encoded_command_alias_and_malformed():
    encoded = base64.b64encode("kollab --agent coder".encode("utf-16le")).decode("ascii")

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
        return_value=SimpleNamespace(error="", success=True, stdout="ready\n", stderr="", exit_code=0)
    )
    command = "printf ready | grep -E ready"

    result = await executor._execute_terminal_command({"type": "terminal", "id": "benign-filter", "command": command})

    assert result.success
    assert result.output == "ready\n"
    executor.shell_executor.run.assert_awaited_once()


def test_generic_encoded_short_alias_blocks_actual_launch():
    encoded = base64.b64encode("kollab --agent coder".encode("utf-16le")).decode("ascii")
    for option in ("-e", "-ec", "-enc"):
        assert _is_blocked_kollab_spawn(f"unknown-shell.exe {option} {encoded}")


@pytest.mark.parametrize("tool", ["grep", "rg", "git grep"])
def test_search_command_can_match_a_guarded_launch_as_text(tool):
    assert not _is_blocked_kollab_spawn(f"{tool} -c 'kollab --agent coder'")


@pytest.mark.parametrize("tool", ["grep", "rg", "git grep"])
def test_search_command_can_match_encoded_launch_as_text(tool):
    encoded = base64.b64encode("kollab --agent coder".encode("utf-16le")).decode("ascii")
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


def test_background_git_config_does_not_treat_repository_path_as_a_launch():
    assert not _is_blocked_kollab_spawn("git -c safe.setting=1 -C /tmp/project/kollab status &")


def test_git_config_command_launch_is_still_blocked():
    assert _is_blocked_kollab_spawn("git -c 'core.pager=kollab --agent coder' status")


def test_git_grep_count_after_global_directory_flag_is_data():
    assert not _is_blocked_kollab_spawn("git -C /tmp/project/kollab grep -c 'kollab --agent coder'")


def test_git_global_directory_does_not_hide_executable_pager():
    assert _is_blocked_kollab_spawn(
        "git -C /tmp/project/kollab grep -e pattern --open-files-in-pager='kollab --agent coder'"
    )


@pytest.mark.parametrize(
    "command",
    [
        "grep -e x `kollab --agent coder`",
        'grep -e x "prefix `kollab --agent coder` suffix"',
        "printf x `kollab --hub stop all`",
    ],
)
def test_backtick_substitutions_do_not_hide_launches(command):
    assert _is_blocked_kollab_spawn(command)


@pytest.mark.parametrize(
    "command",
    [
        "grep -e x '`kollab --agent coder`'",
        r"grep -e x \`kollab --agent coder\`",
    ],
)
def test_literal_backticks_are_not_shell_substitutions(command):
    assert not _is_blocked_kollab_spawn(command)


@pytest.mark.parametrize("option", ["--ec", "--enc", "--encodedcommand"])
def test_unknown_wrapper_double_dash_encoded_command_stays_blocked(option):
    encoded = base64.b64encode("kollab --agent coder".encode("utf-16le")).decode("ascii")
    assert _is_blocked_kollab_spawn(f"unknown-shell.exe {option} {encoded}")


@pytest.mark.parametrize("option", ["-ec", "-enc"])
def test_unknown_wrapper_multichar_encoded_flags_stay_conservative(option):
    assert _is_blocked_kollab_spawn(f"mytool {option} not-base64")


deeply_nested_short_launch = "kollab -a coder"
for _ in range(9):
    deeply_nested_short_launch = f'echo "$({deeply_nested_short_launch})"'

long_literal_substitution = "echo '" + ("x" * 5000) + " $(kollab --agent coder)'"

SUBSTITUTION_GUARD_CASES = [
    pytest.param(
        "echo '$(kollab --agent coder)'", False, False, id="single-quoted-command-text"
    ),
    pytest.param(
        "echo safe # $(kollab --agent coder)",
        False,
        False,
        id="command-substitution-in-shell-comment",
    ),
    pytest.param(
        "echo safe # <(kollab --agent coder)",
        False,
        False,
        id="process-substitution-in-shell-comment",
    ),
    pytest.param(
        "echo safe # `kollab --agent coder`",
        False,
        False,
        id="backtick-substitution-in-shell-comment",
    ),
    pytest.param(
        "echo foo\\ # $(kollab --agent coder)",
        True,
        False,
        id="escaped-space-before-comment-marker",
    ),
    pytest.param(
        "cat <<EOF\n'$(kollab --agent coder)'\nEOF",
        True,
        False,
        id="single-quoted-substitution-in-unquoted-heredoc",
    ),
    pytest.param(
        "cat <<'EOF'\n`kollab --agent coder`\nEOF",
        False,
        False,
        id="backtick-text-in-quoted-heredoc",
    ),
    pytest.param(
        "cat <<EOF\n`kollab --agent coder`\nEOF",
        True,
        False,
        id="backtick-substitution-in-unquoted-heredoc",
    ),
    pytest.param(
        "cat <<EOF\nplain\nEOF\necho safe # $(kollab --agent coder)",
        False,
        False,
        id="comment-after-heredoc-body",
    ),
    pytest.param(
        "echo '<(kollab --agent coder)'",
        False,
        False,
        id="single-quoted-input-process-text",
    ),
    pytest.param(
        'echo "<(kollab --agent coder)"',
        False,
        False,
        id="double-quoted-input-process-text",
    ),
    pytest.param(
        'echo ">(kollab --agent coder)"',
        False,
        False,
        id="double-quoted-output-process-text",
    ),
    pytest.param(
        "echo '\"$(kollab --agent coder)\"'",
        False,
        False,
        id="nested-single-quoted-text",
    ),
    pytest.param(
        r'echo "\$(kollab --agent coder)"',
        False,
        False,
        id="escaped-command-substitution",
    ),
    pytest.param(
        "echo '>(kollab --agent coder)' &", False, False, id="backgrounded-literal-text"
    ),
    pytest.param(
        long_literal_substitution, False, False, id="long-literal-substitution"
    ),
    pytest.param(
        "echo 'é ü $(kollab --agent coder)'",
        False,
        False,
        id="non-ascii-literal-substitution",
    ),
    pytest.param(
        "echo $(kollab --agent coder)", True, False, id="unquoted-command-substitution"
    ),
    pytest.param(
        'echo "$(kollab --agent coder)"',
        True,
        False,
        id="double-quoted-command-substitution",
    ),
    pytest.param(
        "echo \"'$(kollab --agent coder)'\"",
        True,
        False,
        id="single-quotes-inside-double-quotes",
    ),
    pytest.param(
        'echo "\'" $(kollab --agent coder)',
        True,
        False,
        id="single-quote-inside-double-then-launch",
    ),
    pytest.param(
        "echo '\"' $(kollab --agent coder)",
        True,
        False,
        id="double-quote-inside-single-then-launch",
    ),
    pytest.param(
        "cat <(kollab --agent coder)", True, False, id="input-process-substitution"
    ),
    pytest.param(
        "echo >(kollab --agent coder)", True, False, id="output-process-substitution"
    ),
    pytest.param(
        'echo "$(true; (kollab --agent coder))"', True, False, id="nested-parentheses"
    ),
    pytest.param(
        "echo \"$(printf ')'; kollab --agent coder)\"",
        True,
        False,
        id="quoted-closing-parenthesis",
    ),
    pytest.param(
        'echo "$(echo "$(kollab --agent coder)")"',
        True,
        False,
        id="nested-command-substitution",
    ),
    pytest.param(
        'echo "$(printf safe)"',
        False,
        False,
        id="safe-double-quoted-command-substitution",
    ),
    pytest.param(
        'echo "$(kollab --agent coder"',
        True,
        False,
        id="unmatched-command-substitution",
    ),
    pytest.param(
        'echo "$(printf safe # )\nkollab --agent coder\n)"',
        True,
        False,
        id="comment-parenthesis-inside-command-substitution",
    ),
    pytest.param(
        'echo "$(cat <<EOF\n)\nkollab --agent coder\nEOF\n)"',
        True,
        False,
        id="heredoc-parenthesis-inside-command-substitution",
    ),
    pytest.param('echo "$(kollab -a coder"', True, False, id="unmatched-short-launch"),
    pytest.param(
        r"echo $'\'' $(kollab -a coder) 'x'",
        True,
        False,
        id="ansi-c-quote-tokenizer-error",
    ),
    pytest.param(
        r"echo $'it\'s'; kollab -a coder", True, False, id="ansi-c-quote-short-launch"
    ),
    pytest.param(
        deeply_nested_short_launch, True, False, id="deeply-nested-short-launch"
    ),
    pytest.param(
        'bash -c "echo \\$(kollab --agent coder)"', True, False, id="shell-wrapper"
    ),
    pytest.param(
        'python -c \'import subprocess; subprocess.run(["echo", "$(kollab --agent coder)"])\'',
        False,
        False,
        id="python-argv-literal-substitution",
    ),
    pytest.param(
        "python -c 'import subprocess; subprocess.run([\"echo $(kollab --agent coder)\"], shell=True)'",
        True,
        False,
        id="python-shell-true-substitution",
    ),
    pytest.param("kollab --agent coder &", True, False, id="background-command"),
    pytest.param(
        "kollab --agent coder 2>&1", True, False, id="file-descriptor-redirection"
    ),
    pytest.param(
        "echo '$(kollab --agent coder)'", False, True, id="background-flag-literal-text"
    ),
    pytest.param("kollab --agent coder", True, True, id="background-flag-command"),
]


@pytest.mark.parametrize(("command", "blocked", "background"), SUBSTITUTION_GUARD_CASES)
def test_spawn_guard_respects_shell_substitution_context(command, blocked, background):
    assert _is_blocked_kollab_spawn(command, background=background) is blocked


@pytest.mark.asyncio
@pytest.mark.parametrize(("command", "blocked", "background"), SUBSTITUTION_GUARD_CASES)
@pytest.mark.parametrize("use_tmux", [False, True], ids=["shell-executor", "tmux"])
async def test_terminal_dispatch_preserves_shell_substitution_guard(
    command, blocked, background, use_tmux
):
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: (
        key == "tools.block_kollab_terminal_spawn"
    )
    executor = ToolExecutor(MagicMock(), MagicMock(), config=config)
    tmux = MagicMock()
    tmux.execute_foreground = AsyncMock(
        return_value={"success": True, "output": "ok\n", "error": ""}
    )
    tmux.execute_background = AsyncMock(
        return_value={
            "success": True,
            "message": "started",
            "session_name": "substitution-guard",
        }
    )
    executor.tmux_plugin = tmux if use_tmux else None
    executor.shell_executor.run = AsyncMock(
        return_value=SimpleNamespace(
            error="", success=True, stdout="ok\n", stderr="", exit_code=0
        )
    )

    result = await executor._execute_terminal_command(
        {
            "type": "terminal",
            "id": "substitution-guard",
            "command": command,
            "background": background,
        }
    )

    if blocked:
        assert not result.success
        assert "tools.block_kollab_terminal_spawn" in result.error
        executor.shell_executor.run.assert_not_awaited()
        tmux.execute_foreground.assert_not_awaited()
        tmux.execute_background.assert_not_awaited()
    else:
        assert result.success
        if use_tmux and background:
            assert result.output == "started"
            tmux.execute_background.assert_awaited_once()
            tmux.execute_foreground.assert_not_awaited()
            executor.shell_executor.run.assert_not_awaited()
        elif use_tmux:
            assert result.output == "ok\n"
            tmux.execute_foreground.assert_awaited_once()
            tmux.execute_background.assert_not_awaited()
            executor.shell_executor.run.assert_not_awaited()
        else:
            assert result.output == "ok\n"
            executor.shell_executor.run.assert_awaited_once()
            tmux.execute_foreground.assert_not_awaited()
            tmux.execute_background.assert_not_awaited()
