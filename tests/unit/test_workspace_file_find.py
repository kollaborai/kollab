"""The workspace search capability accepts patterns, never executable programs."""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kollabor_agent.file_operations_executor import FileOperationsExecutor
from kollabor_agent.shell_executor import ShellResult
from kollabor_agent.tool_call_contract import normalize_native_tool_call
from kollabor_agent.tool_executor import ToolExecutor
from kollabor_agent.tool_registry import get_registry


@pytest.fixture
def project(tmp_path):
    workspace = tmp_path / "project"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "marker.txt").touch()
    (workspace / "src").mkdir()
    (workspace / "src" / "marker.txt").touch()
    (workspace / "link").symlink_to(outside, target_is_directory=True)
    (workspace / "marker-link.txt").symlink_to(outside / "marker.txt")
    return workspace


def search(workspace, pattern="marker.txt", **extra):
    return FileOperationsExecutor(workspace=workspace).execute_operation(
        {"type": "file_find", "id": "search", "pattern": pattern, **extra}
    )


def test_search_never_follows_workspace_symlinks(project):
    result = search(project)
    assert result["success"], result
    assert json.loads(result["output"]) == {
        "matches": ["src/marker.txt"], "truncated": False
    }


@pytest.mark.parametrize(
    "program",
    [
        "find -L . -name marker.txt -print",
        'printf "%s\\n" "$(find ../outside -name marker.txt -print)"',
        r"find . -maxdepth 1 -exec find ../outside -name marker.txt -print \;",
        "eval 'find ../outside -name marker.txt -print'",
        "timeout 2 find ../outside -name marker.txt -print",
        "python3 -c 'import subprocess; subprocess.run([\"find\", \"../outside\"])'",
        r'f\ind "$HOME" -maxdepth 1 -name marker.txt -print',
        "fi''nd '../outside' -name marker.txt -print",
        'f=fi; n=nd; "$f$n" ../outside -name marker.txt -print',
        'f=fi; n=nd; eval "$f$n ../outside -name marker.txt -print"',
        'awk \'BEGIN { system("find ../outside -name marker.txt -print") }\'',
        'perl -e \'system("fi" . "nd", "../outside")\'',
        "find /Users -maxdepth 1 -type f -print",
        "find / -maxdepth 1 -type f -print",
        "find '$HOME' -name marker.txt -print",
        "find ~ -name marker.txt -print",
        "cat <<EOF\n$(find ../outside -name marker.txt -print)\nEOF",
    ],
)
def test_reviewed_shell_programs_cannot_be_executed_by_search(project, program):
    # Each historic bypass is now either a literal filename glob or an invalid
    # basename. No shell grammar, interpreter or session participates in search.
    with patch("subprocess.Popen") as spawn, patch("os.system") as system:
        result = search(project, program)
    spawn.assert_not_called()
    system.assert_not_called()
    if "/" in program or "\\" in program:
        assert not result["success"]
        assert "basename pattern" in result["error"], result
    else:
        assert result["success"], result
        assert json.loads(result["output"])["matches"] == []


@pytest.mark.parametrize("pattern", [None, "", 1, "../*", "/Users/*", "src/*.py", "a\x00b", "a\\b"])
def test_search_refuses_non_basename_patterns(project, pattern):
    result = search(project, pattern)
    assert not result["success"]
    assert "basename pattern" in result["error"], result


def test_search_treats_shell_metacharacters_as_filename_data(project):
    name = "$(echo marker); eval find"
    (project / name).touch()
    result = search(project, name)
    assert result["success"], result
    assert json.loads(result["output"])["matches"] == [name]


def test_search_cannot_be_redirected_by_extra_arguments_or_access_mode(project):
    executor = FileOperationsExecutor(workspace=project)
    executor.set_path_access_mode("anywhere")
    result = executor.execute_operation({
        "type": "file_find", "pattern": "marker.txt",
        "path": str(project.parent), "cwd": str(project.parent),
        "follow_symlinks": True,
    })
    assert result["success"], result
    assert json.loads(result["output"])["matches"] == ["src/marker.txt"]


def test_search_refuses_a_directory_replaced_with_a_symlink(project):
    real_open = os.open
    swapped = False

    def swap_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "src" and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            (project / "src").rename(project / "original-src")
            (project / "src").symlink_to(project.parent / "outside", target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)

    with patch("os.open", side_effect=swap_before_open):
        result = search(project)
    assert swapped
    assert not result["success"]  # A raced traversal fails visibly, never follows.
    assert "Operation failed" in result["error"]


def test_search_reports_truncation_instead_of_an_apparently_complete_result(project):
    for index in range(205):
        (project / f"match-{index}.txt").touch()
    result = search(project, "match-*.txt")
    assert result["success"], result
    output = json.loads(result["output"])
    assert len(output["matches"]) == 200
    assert output["truncated"] is True


def test_search_fails_closed_when_descriptor_traversal_is_unavailable(project):
    with patch("os.supports_fd", set()):
        result = search(project)
    assert not result["success"]
    assert "unavailable" in result["error"], result


@pytest.mark.asyncio
async def test_native_search_uses_the_active_workspace_and_normal_tool_hooks(project):
    bus = MagicMock()
    bus.emit_with_hooks = AsyncMock(return_value=None)
    executor = ToolExecutor(mcp_integration=MagicMock(), event_bus=bus, workspace=project)
    executor.shell_executor.run = AsyncMock()
    executor.shell_executor.run_argv = AsyncMock()
    # An old file executor must not choose a different root after workspace changes.
    executor.file_ops_executor.project_root = project.parent / "outside"
    tool = normalize_native_tool_call({
        "id": "search", "name": "file_find", "arguments": {"pattern": "marker.txt"}
    })
    result = await executor.execute_tool(tool)
    assert result.success, result.error
    assert json.loads(result.output)["matches"] == ["src/marker.txt"]
    assert bus.emit_with_hooks.await_count >= 2
    executor.shell_executor.run.assert_not_awaited()
    executor.shell_executor.run_argv.assert_not_awaited()


def test_registered_search_has_no_command_root_or_session_capability():
    tool = get_registry().get("file-find")
    assert tool is not None
    schema = tool.to_json_schema()
    assert schema["name"] == "file_find"
    assert set(schema["parameters"]["properties"]) == {"pattern"}
    assert "file-find" in get_registry().get("terminal").notes


@pytest.mark.asyncio
@pytest.mark.parametrize("send", [False, True])
async def test_general_terminals_preserve_harmless_programs_outside_search_scope(project, send):
    executor = ToolExecutor(mcp_integration=MagicMock(), event_bus=MagicMock(), workspace=project)
    command = "printf '%s\\n' find | cat"
    if send:
        executor.tmux_plugin = MagicMock()
        executor.tmux_plugin.send_to_session.return_value = {"success": True}
        result = await executor._execute_terminal_command({
            "type": "terminal_send", "id": "send", "session_name": "shell", "text": command,
        })
        assert result.success, result.error
        executor.tmux_plugin.send_to_session.assert_called_once_with("shell", command)
    else:
        executor.shell_executor.run = AsyncMock(return_value=ShellResult(
            success=True, stdout="find\n", stderr="", exit_code=0
        ))
        result = await executor._execute_terminal_command({
            "type": "terminal", "id": "print", "command": command,
        })
        assert result.success, result.error
        executor.shell_executor.run.assert_awaited_once()
