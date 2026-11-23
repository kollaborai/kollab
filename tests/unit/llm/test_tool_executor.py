"""Tests for ToolExecutor component.

Tests terminal command execution, MCP tool calls,
and result formatting functionality.
"""

import asyncio
import os
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kollabor_agent.shell_executor import ShellResult
from kollabor_agent.tool_executor import ToolExecutor
from kollabor_events.models import EventType


# Mock result classes for testing
@dataclass
class MockToolResult:
    """Mock tool execution result."""

    tool_type: str
    tool_id: str
    success: bool
    output: Optional[str] = None
    error: Optional[str] = None
    execution_time: float = 0.0
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    def to_dict(self):
        return {
            "tool_type": self.tool_type,
            "tool_id": self.tool_id,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "execution_time": self.execution_time,
        }


class TestToolExecutor(unittest.IsolatedAsyncioTestCase):
    """Test tool execution functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.mcp_integration = MagicMock()
        self.event_bus = MagicMock()
        self.event_bus.emit_with_hooks = AsyncMock(return_value=None)
        self.terminal_timeout = 10
        self.mcp_timeout = 20

        self.executor = ToolExecutor(
            mcp_integration=self.mcp_integration,
            event_bus=self.event_bus,
            terminal_timeout=self.terminal_timeout,
            mcp_timeout=self.mcp_timeout,
        )

    async def test_livekit_destructive_command_is_blocked_before_permission_or_spawn(self):
        from tests.unit.llm.voice_admission_helpers import make_voice_origin_context

        origin_context = make_voice_origin_context("turn-1")
        tool_data = {
            "type": "terminal",
            "id": "voice-terminal-1",
            "command": "git push origin main",
        }
        self.executor.shell_executor.run_argv = AsyncMock()
        terminal_command = AsyncMock()
        self.executor._execute_terminal_command = terminal_command

        result = await self.executor.execute_tool(
            tool_data,
            execution_context=origin_context,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.metadata["reason_code"], "command_blocked")
        self.event_bus.emit_with_hooks.assert_not_awaited()
        self.executor.shell_executor.run_argv.assert_not_awaited()
        terminal_command.assert_not_awaited()

    async def test_livekit_read_only_terminal_keeps_permission_hook_and_uses_argv(self):
        from tests.unit.llm.voice_admission_helpers import make_voice_origin_context

        origin_context = make_voice_origin_context("turn-1")
        self.executor.shell_executor.run_argv = AsyncMock(
            return_value=ShellResult(success=True, stdout="clean\n", stderr="", exit_code=0)
        )
        terminal_command = AsyncMock()
        self.executor._execute_terminal_command = terminal_command

        result = await self.executor.execute_tool(
            {"type": "terminal", "id": "voice-terminal-2", "command": "git status --short"},
            execution_context=origin_context,
        )

        self.assertTrue(result.success)
        self.event_bus.emit_with_hooks.assert_awaited()
        self.executor.shell_executor.run_argv.assert_awaited_once()
        terminal_command.assert_not_awaited()

    async def test_livekit_read_only_terminal_honors_requested_timeout(self):
        from tests.unit.llm.voice_admission_helpers import make_voice_origin_context

        self.executor.shell_executor.run_argv = AsyncMock(
            return_value=ShellResult(success=True, stdout="clean\n", stderr="", exit_code=0)
        )

        result = await self.executor.execute_tool(
            {
                "type": "terminal",
                "id": "voice-terminal-timeout",
                "command": "git status --short",
                "timeout": "1s",
            },
            execution_context=make_voice_origin_context("turn-timeout"),
        )

        self.assertTrue(result.success)
        self.executor.shell_executor.run_argv.assert_awaited_once()
        self.assertEqual(self.executor.shell_executor.run_argv.await_args.kwargs["timeout"], 1)

    async def test_livekit_terminal_rejects_invalid_timeout_before_running(self):
        from tests.unit.llm.voice_admission_helpers import make_voice_origin_context

        self.executor.shell_executor.run_argv = AsyncMock(
            return_value=ShellResult(success=True, stdout="clean\n", stderr="", exit_code=0)
        )

        result = await self.executor.execute_tool(
            {
                "type": "terminal",
                "id": "voice-terminal-invalid-timeout",
                "command": "git status --short",
                "timeout": "thirty seconds",
            },
            execution_context=make_voice_origin_context("turn-invalid-timeout"),
        )

        self.assertFalse(result.success)
        self.assertIn("timeout", result.error.lower())
        self.executor.shell_executor.run_argv.assert_not_awaited()


    async def test_livekit_path_qualified_executable_never_reaches_process(self):
        from tests.unit.llm.voice_admission_helpers import make_voice_origin_context

        origin_context = make_voice_origin_context("path-escape-turn")
        self.executor.shell_executor.run_argv = AsyncMock()

        result = await self.executor.execute_tool(
            {
                "type": "terminal",
                "id": "voice-path-escape",
                "command": "./ls -l .",
            },
            execution_context=origin_context,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.metadata["reason_code"], "command_blocked")
        self.event_bus.emit_with_hooks.assert_not_awaited()
        self.executor.shell_executor.run_argv.assert_not_awaited()


    async def test_execute_terminal_command_success(self):
        """Test successful terminal command execution."""
        tool_data = {
            "type": "terminal",
            "id": "terminal_0",
            "command": "echo 'Hello World'",
            "raw": "<terminal>echo 'Hello World'</terminal>",
        }

        self.executor.shell_executor.run = AsyncMock(
            return_value=ShellResult(
                success=True, stdout="Hello World\n", stderr="", exit_code=0
            )
        )

        result = await self.executor._execute_terminal_command(tool_data)

        self.assertTrue(result.success)
        self.assertEqual(result.tool_type, "terminal")
        self.assertEqual(result.tool_id, "terminal_0")
        self.assertEqual(result.output.strip(), "Hello World")
        self.assertEqual(result.error, "")
        self.assertGreaterEqual(result.execution_time, 0)

    async def test_terminal_find_refuses_roots_outside_project_before_execution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            outside_home = root / "home"
            workspace.mkdir()
            outside_home.mkdir()
            self.executor.workspace = workspace
            self.executor.shell_executor.run = AsyncMock(
                return_value=ShellResult(
                    success=True,
                    stdout="outside fixture\n",
                    stderr="",
                    exit_code=0,
                )
            )

            commands = [
                f'find "{outside_home}" -maxdepth 2 -type f -print',
                'find "$HOME" -maxdepth 2 -type f -print',
                "find /Users -maxdepth 2 -type f -print",
                "find / -maxdepth 2 -type f -print",
                "find ../../ -maxdepth 2 -type f -print",
                "echo ready; find / -maxdepth 2 -type f -print",
                f'find -E "{outside_home}" -name marker.txt -print',
                f'printf "%s\\n" "$(find "{outside_home}" -name marker.txt -print)"',
                rf'find . -maxdepth 1 -exec find "{outside_home}" -name marker.txt -print \;',
                f'cd "{outside_home}" && find . -name marker.txt -print',
                f'find {{{workspace},{outside_home}}} -name marker.txt -print',
                "find . -follow -name marker.txt -print",
                f'env -C "{outside_home}" find . -name marker.txt -print',
                f"sh -c 'find \"{outside_home}\" -name marker.txt -print'",
                f"eval 'find \"{outside_home}\" -name marker.txt -print'",
                f'timeout 2 find "{outside_home}" -name marker.txt -print',
                f'env -S "find {outside_home} -name marker.txt -print"',
                f'env --split-string="find {outside_home} -name marker.txt -print"',
                f'/usr/bin/env -S "find {outside_home} -name marker.txt -print"',
            ]
            with patch.dict(os.environ, {"HOME": str(outside_home)}):
                for command in commands:
                    with self.subTest(command=command):
                        result = await self.executor._execute_terminal_command(
                            {"type": "terminal", "id": "find-outside", "command": command}
                        )

                        self.assertFalse(result.success)
                        self.assertIn("search paths must stay inside the active workspace", result.error)

            self.executor.shell_executor.run.assert_not_awaited()

    async def test_terminal_find_refuses_shell_escaped_executable_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            outside = root / "outside"
            workspace.mkdir()
            outside.mkdir()
            (outside / "marker.txt").write_text("outside fixture\n")
            self.executor.workspace = workspace
            self.executor.shell_executor.run = AsyncMock(
                return_value=ShellResult(
                    success=True,
                    stdout="outside fixture\n",
                    stderr="",
                    exit_code=0,
                )
            )

            commands = [
                f'f\\ind "{outside}" -name marker.txt -print',
                f"fi''nd '{outside}' -name marker.txt -print",
            ]
            for command in commands:
                with self.subTest(command=command):
                    result = await self.executor._execute_terminal_command(
                        {
                            "type": "terminal",
                            "id": "find-escaped-name",
                            "command": command,
                            "cwd": str(workspace),
                        }
                    )

                    self.assertFalse(result.success)
                    self.assertIn(
                        "search paths must stay inside the active workspace",
                        result.error,
                    )
                    self.executor.shell_executor.run.assert_not_awaited()

    async def test_terminal_find_refuses_shell_expanded_executable_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            self.executor.workspace = workspace
            self.executor.shell_executor.run = AsyncMock(
                return_value=ShellResult(
                    success=True,
                    stdout="outside fixture\n",
                    stderr="",
                    exit_code=0,
                )
            )
            commands = [
                'f=fi; n=nd; "$f$n" ../outside -name marker.txt -print',
                "bash -c 'f=fi; n=nd; \"$f$n\" ../outside -name marker.txt -print'",
                "timeout 2 bash -c 'f=fi; n=nd; \"$f$n\" ../outside -name marker.txt -print'",
            ]
            for command in commands:
                with self.subTest(command=command):
                    result = await self.executor._execute_terminal_command(
                        {
                            "type": "terminal",
                            "id": "find-expanded-name",
                            "command": command,
                        }
                    )

                    self.assertFalse(result.success)
                    self.assertIn(
                        "search paths must stay inside the active workspace",
                        result.error,
                    )
            self.executor.shell_executor.run.assert_not_awaited()

    async def test_terminal_find_mention_in_echo_is_not_executed_as_find(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            self.executor.workspace = workspace
            self.executor.shell_executor.run = AsyncMock(
                return_value=ShellResult(
                    success=True, stdout="find ../outside -print\n", stderr="", exit_code=0
                )
            )

            result = await self.executor._execute_terminal_command(
                {
                    "type": "terminal",
                    "id": "find-mention",
                    "command": "echo 'find ../outside -print'",
                }
            )

            self.assertTrue(result.success)
            self.executor.shell_executor.run.assert_awaited_once()

    async def test_terminal_find_refuses_uninspectable_interpreter_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            self.executor.workspace = workspace
            self.executor.shell_executor.run = AsyncMock(
                return_value=ShellResult(
                    success=True,
                    stdout="../outside/marker.txt\n",
                    stderr="",
                    exit_code=0,
                )
            )
            command = (
                "python3 -c 'import subprocess; "
                "subprocess.run([\"find\", \"../outside\", \"-name\", "
                "\"marker.txt\", \"-print\"], check=True)'"
            )

            result = await self.executor._execute_terminal_command(
                {"type": "terminal", "id": "find-interpreter", "command": command}
            )

            self.executor.shell_executor.run.assert_not_awaited()
            self.assertFalse(result.success)
            self.assertIn(
                "search paths must stay inside the active workspace", result.error
            )

    async def test_terminal_find_refuses_symlinked_root_outside_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            outside = root / "outside"
            workspace.mkdir()
            outside.mkdir()
            outside_link = workspace / "outside-link"
            outside_link.symlink_to(outside, target_is_directory=True)
            self.executor.workspace = workspace
            self.executor.shell_executor.run = AsyncMock()

            result = await self.executor._execute_terminal_command(
                {
                    "type": "terminal",
                    "id": "find-symlink-outside",
                    "command": f'find -L "{outside_link}" -maxdepth 2 -type f -print',
                }
            )

            self.assertFalse(result.success)
            self.assertIn("search paths must stay inside the active workspace", result.error)
            self.executor.shell_executor.run.assert_not_awaited()

    async def test_terminal_find_refuses_following_workspace_symlinks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            outside = root / "outside"
            workspace.mkdir()
            outside.mkdir()
            (workspace / "outside-link").symlink_to(outside, target_is_directory=True)
            self.executor.workspace = workspace
            self.executor.shell_executor.run = AsyncMock(
                return_value=ShellResult(
                    success=True,
                    stdout="outside fixture\n",
                    stderr="",
                    exit_code=0,
                )
            )

            result = await self.executor._execute_terminal_command(
                {
                    "type": "terminal",
                    "id": "find-follow-workspace-symlink",
                    "command": "find -L . -name marker.txt -print",
                }
            )

            self.assertFalse(result.success)
            self.assertIn("search paths must stay inside the active workspace", result.error)
            self.executor.shell_executor.run.assert_not_awaited()

    async def test_terminal_find_allows_rooted_workspace_search(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "project"
            workspace.mkdir()
            self.executor.workspace = workspace
            self.executor.shell_executor.run = AsyncMock(
                return_value=ShellResult(
                    success=True, stdout="src/main.py\n", stderr="", exit_code=0
                )
            )
            command = "find . -maxdepth 2 -type f -print"

            result = await self.executor._execute_terminal_command(
                {"type": "terminal", "id": "find-inside", "command": command}
            )

            self.assertTrue(result.success)
            self.assertEqual(result.output, "src/main.py\n")
            self.executor.shell_executor.run.assert_awaited_once_with(
                command, timeout=self.terminal_timeout, cwd=workspace
            )

    async def test_terminal_read_only_tool_allows_reads_and_refuses_writes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            from kollabor_agent.tool_registry import get_registry

            self.assertIsNotNone(get_registry().get("terminal-read"))
            self.executor.workspace = workspace
            self.executor.set_bundle_scope(
                ["file-read", "file-grep", "terminal-read"]
            )
            self.executor.shell_executor.run_argv = AsyncMock(
                return_value=ShellResult(
                    success=True, stdout="commit\n", stderr="", exit_code=0
                )
            )

            read_result = await self.executor.execute_tool(
                {
                    "type": "terminal_read",
                    "id": "terminal-read-1",
                    "command": "git log --oneline -1",
                    "cwd": str(workspace),
                }
            )
            write_result = await self.executor.execute_tool(
                {
                    "type": "terminal_read",
                    "id": "terminal-read-2",
                    "command": "touch x.txt",
                    "cwd": str(workspace),
                }
            )

            self.assertTrue(read_result.success)
            self.assertEqual(read_result.output, "commit\n")
            self.assertFalse(write_result.success)
            self.assertIn("read-only", write_result.error.lower())
            self.assertFalse((workspace / "x.txt").exists())
            self.executor.shell_executor.run_argv.assert_awaited_once()
            call = self.executor.shell_executor.run_argv.await_args
            self.assertEqual(Path(call.args[0][0]).name, "git")
            self.assertTrue(Path(call.args[0][0]).is_absolute())
            self.assertEqual(
                call.args[0][1:4], ("-c", "core.fsmonitor=false", "log")
            )
            self.assertEqual(call.args[0][4:], ("--oneline", "-1"))
            self.assertEqual(call.kwargs["timeout"], 10)
            self.assertEqual(call.kwargs["cwd"], workspace.resolve())
            self.assertEqual(call.kwargs["max_output_bytes"], 8 * 1024)

    async def test_terminal_read_ignores_inherited_git_repository_overrides(self):
        import subprocess

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            workspace = temp_root / "workspace"
            outside = temp_root / "outside"
            for repository, message in (
                (workspace, "workspace-marker"),
                (outside, "outside-marker"),
            ):
                repository.mkdir()
                subprocess.run(["git", "init", "-q", str(repository)], check=True)
                (repository / "README.md").write_text(message)
                subprocess.run(
                    ["git", "-C", str(repository), "add", "README.md"], check=True
                )
                subprocess.run(
                    [
                        "git", "-C", str(repository), "-c", "user.name=Review",
                        "-c", "user.email=review@example.invalid", "commit", "-qm", message,
                    ],
                    check=True,
                )

            self.executor.workspace = workspace
            with patch.dict(os.environ, {"GIT_DIR": str(outside / ".git")}):
                result = await self.executor.execute_tool(
                    {
                        "type": "terminal_read",
                        "id": "terminal-read-inherited-git-dir",
                        "command": "git log --format=%s -1",
                    }
                )

            self.assertTrue(result.success)
            self.assertEqual(result.output.strip(), "workspace-marker")

    async def test_terminal_read_disables_repository_fsmonitor_helper(self):
        import subprocess

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            helper = workspace / "fsmonitor-helper"
            marker = workspace / "fsmonitor-ran"
            helper.write_text(
                f"#!/bin/sh\nprintf ran > '{marker}'\nprintf 'token\\n'\n"
            )
            helper.chmod(0o755)
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            subprocess.run(
                ["git", "-C", str(workspace), "config", "core.fsmonitor", str(helper)],
                check=True,
            )
            self.executor.workspace = workspace

            result = await self.executor.execute_tool(
                {
                    "type": "terminal_read",
                    "id": "terminal-read-fsmonitor",
                    "command": "git status --short",
                }
            )

            self.assertTrue(result.success, result.error)
            self.assertFalse(marker.exists())

    async def test_terminal_read_refuses_git_diff_no_index_outside_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.executor.workspace = Path(temp_dir)
            self.executor.shell_executor.run_argv = AsyncMock(
                return_value=ShellResult(
                    success=True, stdout="outside contents", stderr="", exit_code=0
                )
            )

            result = await self.executor.execute_tool(
                {
                    "type": "terminal_read",
                    "id": "terminal-read-no-index-outside",
                    "command": "git diff --no-index /etc/passwd /etc/hosts",
                }
            )

            self.assertFalse(result.success)
            self.assertIn("project", result.error.lower())
            self.executor.shell_executor.run_argv.assert_not_awaited()

    async def test_terminal_read_git_diff_disables_configured_external_helper(self):
        import subprocess

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            helper = workspace / "external-diff"
            marker = workspace / "helper-ran"
            helper.write_text(f"#!/bin/sh\nprintf ran > '{marker}'\n")
            helper.chmod(0o755)
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            subprocess.run(
                ["git", "-C", str(workspace), "config", "user.name", "Review"],
                check=True,
            )
            subprocess.run(
                [
                    "git", "-C", str(workspace), "config", "user.email",
                    "review@example.invalid",
                ],
                check=True,
            )
            subprocess.run(
                [
                    "git", "-C", str(workspace), "config", "diff.external",
                    str(helper),
                ],
                check=True,
            )
            tracked = workspace / "file.txt"
            tracked.write_text("before\n")
            subprocess.run(
                ["git", "-C", str(workspace), "add", "file.txt"], check=True
            )
            subprocess.run(
                ["git", "-C", str(workspace), "commit", "-qm", "initial"],
                check=True,
            )
            tracked.write_text("after\n")
            self.executor.workspace = workspace

            result = await self.executor.execute_tool(
                {
                    "type": "terminal_read",
                    "id": "terminal-read-external-diff",
                    "command": "git diff -- file.txt",
                }
            )

            self.assertTrue(result.success)
            self.assertFalse(marker.exists())

    async def test_terminal_read_filters_git_overrides_for_windows_executable_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.executor.workspace = Path(temp_dir)
            self.executor.shell_executor.run_argv = AsyncMock(
                return_value=ShellResult(
                    success=True, stdout="commit\n", stderr="", exit_code=0
                )
            )

            with patch(
                "kollabor_agent.tool_executor._resolve_trusted_executable",
                return_value=Path("/trusted/git.exe"),
            ), patch.dict(os.environ, {"GIT_DIR": "/outside/.git"}):
                result = await self.executor.execute_tool(
                    {
                        "type": "terminal_read",
                        "id": "terminal-read-windows-git-name",
                        "command": "git log --oneline -1",
                    }
                )

            self.assertTrue(result.success)
            child_env = self.executor.shell_executor.run_argv.await_args.kwargs["env"]
            self.assertFalse(
                any(name.upper().startswith("GIT_") for name in child_env)
            )

    async def test_terminal_read_resolves_executable_outside_writable_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            fake_bin = Path(temp_dir) / "bin"
            fake_bin.mkdir()
            fake_git = fake_bin / "git"
            fake_git.write_text("#!/bin/sh\nexit 99\n")
            fake_git.chmod(0o755)
            self.executor.workspace = workspace
            self.executor.shell_executor.run_argv = AsyncMock(
                return_value=ShellResult(
                    success=True, stdout="commit\n", stderr="", exit_code=0
                )
            )

            path = os.pathsep.join(
                [str(fake_bin), os.environ.get("PATH", "")]
            )
            with patch.dict(os.environ, {"PATH": path}):
                result = await self.executor.execute_tool(
                    {
                        "type": "terminal_read",
                        "id": "terminal-read-path-1",
                        "command": "git log --oneline -1",
                    }
                )

            self.assertTrue(result.success)
            self.executor.shell_executor.run_argv.assert_awaited_once()
            argv = self.executor.shell_executor.run_argv.await_args.args[0]
            self.assertTrue(Path(argv[0]).is_absolute())
            self.assertNotEqual(Path(argv[0]).resolve(), fake_git.resolve())

    async def test_terminal_read_refuses_recursive_symlink_following_ls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            workspace = temp_root / "workspace"
            private = temp_root / "private"
            workspace.mkdir()
            private.mkdir()
            if os.name != "nt":
                (private / "secret.txt").write_text("outside project")
                (workspace / "external").symlink_to(
                    private, target_is_directory=True
                )
            self.executor.workspace = workspace
            self.executor.shell_executor.run_argv = AsyncMock(
                return_value=ShellResult(
                    success=True, stdout="", stderr="", exit_code=0
                )
            )

            result = await self.executor.execute_tool(
                {
                    "type": "terminal_read",
                    "id": "terminal-read-ls-1",
                    "command": "ls -RL",
                }
            )

            self.assertFalse(result.success)
            self.assertIn("recursive", result.error.lower())
            self.executor.shell_executor.run_argv.assert_not_awaited()

    async def test_terminal_output_uses_the_only_active_session_when_name_is_missing(self):
        plugin = MagicMock()
        plugin.get_session_status = AsyncMock(
            return_value={
                "success": True,
                "sessions": [
                    {"name": "build", "status": "alive", "command": "pytest"},
                    {"name": "old", "status": "dead", "command": "true"},
                ],
            }
        )
        plugin.capture_session_output = AsyncMock(
            return_value={"success": True, "output": ["done"]}
        )
        self.executor.tmux_plugin = plugin

        result = await self.executor.execute_tool(
            {"type": "terminal_output", "id": "output-1", "lines": 10}
        )

        self.assertTrue(result.success)
        self.assertIn("build", result.output)
        plugin.capture_session_output.assert_awaited_once_with("build", lines=10)

    async def test_terminal_output_lists_active_sessions_when_name_is_ambiguous(self):
        plugin = MagicMock()
        plugin.get_session_status = AsyncMock(
            return_value={
                "success": True,
                "sessions": [
                    {"name": "api", "status": "alive"},
                    {"name": "worker", "status": "alive"},
                ],
            }
        )
        self.executor.tmux_plugin = plugin

        result = await self.executor.execute_tool(
            {"type": "terminal_output", "id": "output-2"}
        )

        self.assertFalse(result.success)
        self.assertIn("api", result.error)
        self.assertIn("worker", result.error)
        self.assertIn("terminal_output", result.error)
        plugin.capture_session_output.assert_not_called()

    async def test_terminal_output_reports_when_no_active_session_exists(self):
        plugin = MagicMock()
        plugin.get_session_status = AsyncMock(
            return_value={
                "success": True,
                "sessions": [{"name": "finished", "status": "dead"}],
            }
        )
        self.executor.tmux_plugin = plugin

        result = await self.executor.execute_tool(
            {"type": "terminal_output", "id": "output-3"}
        )

        self.assertFalse(result.success)
        self.assertIn("no active terminal sessions", result.error)
        self.assertIn("terminal-status", result.error)
        plugin.capture_session_output.assert_not_called()

    async def test_terminal_output_reports_session_list_failure(self):
        plugin = MagicMock()
        plugin.get_session_status = AsyncMock(
            return_value={"success": False, "message": "terminal unavailable"}
        )
        self.executor.tmux_plugin = plugin

        result = await self.executor.execute_tool(
            {"type": "terminal_output", "id": "output-4"}
        )

        self.assertFalse(result.success)
        self.assertIn("unable to list active terminal sessions", result.error)
        self.assertIn("terminal unavailable", result.error)
        plugin.capture_session_output.assert_not_called()

    async def test_terminal_send_routes_input_to_named_session(self):
        from kollabor_agent.tool_call_contract import normalize_native_tool_call

        plugin = MagicMock()
        plugin.send_to_session.return_value = {
            "success": True,
            "message": "Input sent to session 'terminal'",
        }
        self.executor.tmux_plugin = plugin
        tool_data = normalize_native_tool_call(
            {
                "name": "terminal_send",
                "arguments": {"session_name": "terminal", "text": "y"},
            }
        )

        result = await self.executor.execute_tool(tool_data)

        self.assertTrue(result.success)
        plugin.send_to_session.assert_called_once_with("terminal", "y")

    async def test_terminal_send_rejects_native_calls_without_session_name(self):
        from kollabor_agent.tool_call_contract import normalize_native_tool_call

        plugin = MagicMock()
        self.executor.tmux_plugin = plugin
        tool_data = normalize_native_tool_call(
            {"name": "terminal_send", "arguments": {"text": "y"}}
        )

        result = await self.executor.execute_tool(tool_data)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Session name required")
        plugin.send_to_session.assert_not_called()

    async def test_file_read_context_hash_metadata_is_preserved(self):
        self.executor.file_ops_executor.execute_operation = MagicMock(
            return_value={
                "success": True,
                "output": "rendered file output",
                "file_content_hash": "raw-hash",
            }
        )

        result = await self.executor._execute_file_operation(
            {
                "type": "file_read",
                "id": "read-1",
                "file": "/workspace/large.py",
            }
        )

        self.assertEqual(result.metadata["file_content_hash"], "raw-hash")

    async def test_legacy_git_alias_routes_to_terminal(self):
        """The legacy native git alias must not reach unknown-type dispatch."""
        tool_data = {
            "type": "git",
            "id": "git_0",
            "command": "git status --short",
        }
        self.event_bus.emit_with_hooks = AsyncMock(return_value=None)

        with patch.object(
            self.executor,
            "_execute_terminal_command",
            new=AsyncMock(
                return_value=MockToolResult("terminal", "git_0", True, " M README.md")
            ),
        ) as mock_terminal:
            result = await self.executor._execute_tool_inner(tool_data)

        self.assertTrue(result.success)
        self.assertEqual(result.tool_type, "terminal")
        mock_terminal.assert_awaited_once_with(
            {"type": "terminal", "id": "git_0", "command": "git status --short"}
        )

    async def test_permission_wait_is_not_tool_run_time(self):
        """The pre hook can sit on a permission prompt; that wait is not timed."""
        tool_data = {"type": "terminal", "id": "terminal_0", "command": "ls"}

        async def hooks(event_type, *args, **kwargs):
            if event_type == EventType.TOOL_CALL_PRE:
                await asyncio.sleep(0.3)  # the user reading the prompt

        self.event_bus.emit_with_hooks = AsyncMock(side_effect=hooks)
        with patch.object(
            self.executor,
            "_execute_terminal_command",
            new=AsyncMock(
                return_value=MockToolResult("terminal", "terminal_0", True, "ok")
            ),
        ):
            result = await self.executor._execute_tool_inner(tool_data)

        self.assertTrue(result.success)
        self.assertLess(result.execution_time, 0.2)

    async def test_execute_terminal_command_failure(self):
        """Test failed terminal command execution."""
        tool_data = {
            "type": "terminal",
            "id": "terminal_0",
            "command": "nonexistent_command",
            "raw": "<terminal>nonexistent_command</terminal>",
        }

        self.executor.shell_executor.run = AsyncMock(
            return_value=ShellResult(
                success=False,
                stdout="",
                stderr="command not found",
                exit_code=1,
                error="Exit code 1: command not found",
            )
        )

        result = await self.executor._execute_terminal_command(tool_data)

        self.assertFalse(result.success)
        self.assertEqual(result.tool_type, "terminal")
        self.assertIn("command not found", result.error)

    async def test_execute_terminal_command_timeout(self):
        """Test terminal command timeout handling."""
        tool_data = {
            "type": "terminal",
            "id": "terminal_0",
            "command": "sleep 20",  # Longer than timeout
            "raw": "<terminal>sleep 20</terminal>",
        }

        self.executor.shell_executor.run = AsyncMock(
            return_value=ShellResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=-1,
                timed_out=True,
                error="Command timed out after 10s",
            )
        )

        result = await self.executor._execute_terminal_command(tool_data)

        self.assertFalse(result.success)
        self.assertIn("timed out", result.error.lower())

    async def test_execute_tool_honors_foreground_timeout_without_tmux(self):
        self.executor.terminal_timeout = 120
        self.executor.shell_executor.run = AsyncMock(
            return_value=ShellResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=-1,
                timed_out=True,
                error="Command timed out after 30s",
            )
        )

        result = await self.executor.execute_tool(
            {
                "type": "terminal",
                "id": "terminal-timeout-fallback",
                "command": "sleep 60",
                "timeout": "30s",
            }
        )

        self.assertFalse(result.success)
        self.executor.shell_executor.run.assert_awaited_once_with(
            "sleep 60", timeout=30, cwd=None
        )

    async def test_execute_tool_honors_foreground_timeout_with_tmux(self):
        self.executor.terminal_timeout = 120
        self.executor.tmux_plugin = MagicMock()
        self.executor.tmux_plugin.execute_foreground = AsyncMock(
            return_value={
                "success": False,
                "output": "",
                "error": "Command timed out after 30s",
                "exit_code": -1,
            }
        )

        result = await self.executor.execute_tool(
            {
                "type": "terminal",
                "id": "terminal-timeout-tmux",
                "command": "sleep 60",
                "timeout": "30s",
            }
        )

        self.assertFalse(result.success)
        self.executor.tmux_plugin.execute_foreground.assert_awaited_once_with(
            command="sleep 60", timeout=30, cwd=None
        )

    async def test_execute_tool_rejects_timeout_above_configured_limit_without_tmux(self):
        self.executor.terminal_timeout = 120
        self.executor.shell_executor.run = AsyncMock(
            return_value=ShellResult(success=True, stdout="", stderr="", exit_code=0)
        )

        result = await self.executor.execute_tool(
            {
                "type": "terminal",
                "id": "terminal-timeout-over-limit-fallback",
                "command": "sleep 60",
                "timeout": "1000h",
            }
        )

        self.executor.shell_executor.run.assert_not_awaited()
        self.assertFalse(result.success)
        self.assertIn("configured", result.error.lower())

    async def test_execute_tool_rejects_timeout_above_configured_limit_with_tmux(self):
        self.executor.terminal_timeout = 120
        self.executor.tmux_plugin = MagicMock()
        self.executor.tmux_plugin.execute_foreground = AsyncMock(
            return_value={"success": True, "output": "", "error": "", "exit_code": 0}
        )

        result = await self.executor.execute_tool(
            {
                "type": "terminal",
                "id": "terminal-timeout-over-limit-tmux",
                "command": "sleep 60",
                "timeout": "1000h",
            }
        )

        self.executor.tmux_plugin.execute_foreground.assert_not_awaited()
        self.assertFalse(result.success)
        self.assertIn("configured", result.error.lower())

    async def test_background_shell_fallback_caps_timeout_without_tmux(self):
        self.executor.terminal_timeout = 120
        self.executor.shell_executor.run = AsyncMock(
            return_value=ShellResult(success=True, stdout="", stderr="", exit_code=0)
        )

        result = await self.executor.execute_tool(
            {
                "type": "terminal",
                "id": "background-timeout-fallback",
                "command": "sleep 1",
                "background": True,
                "timeout": "1000h",
            }
        )

        self.assertTrue(result.success)
        self.executor.shell_executor.run.assert_awaited_once_with(
            "sleep 1", timeout=120, cwd=None
        )

    async def test_background_shell_fallback_caps_timeout_after_tmux_error(self):
        self.executor.terminal_timeout = 120
        self.executor.tmux_plugin = MagicMock()
        self.executor.tmux_plugin.execute_background = AsyncMock(
            side_effect=RuntimeError("tmux unavailable")
        )
        self.executor.shell_executor.run = AsyncMock(
            return_value=ShellResult(success=True, stdout="", stderr="", exit_code=0)
        )

        result = await self.executor.execute_tool(
            {
                "type": "terminal",
                "id": "background-timeout-tmux-fallback",
                "command": "sleep 1",
                "background": True,
                "timeout": "1000h",
            }
        )

        self.assertTrue(result.success)
        self.executor.tmux_plugin.execute_background.assert_awaited_once()
        self.executor.shell_executor.run.assert_awaited_once_with(
            "sleep 1", timeout=120, cwd=None
        )

    async def test_concurrent_terminal_timeouts_do_not_interrupt_sibling(self):
        short = {
            "type": "terminal",
            "id": "short-timeout",
            "command": "sleep 2",
            "timeout": "1s",
        }
        long = {
            "type": "terminal",
            "id": "long-timeout",
            "command": "sleep 1",
            "timeout": "4s",
        }

        _, long_result = await asyncio.gather(
            self.executor.execute_tool(short), self.executor.execute_tool(long)
        )
        await asyncio.sleep(2)

        self.assertTrue(long_result.success, long_result.error)

    async def test_terminal_status_does_not_wait_for_foreground_execution(self):
        foreground_started = asyncio.Event()
        release_foreground = asyncio.Event()
        status_called = asyncio.Event()

        async def execute_foreground(**kwargs):
            foreground_started.set()
            await release_foreground.wait()
            return {"success": True, "output": "done", "error": "", "exit_code": 0}

        async def get_session_status(session_name):
            status_called.set()
            return {
                "success": True,
                "status": "running",
                "command": session_name,
            }

        self.executor.tmux_plugin = MagicMock()
        self.executor.tmux_plugin.execute_foreground = AsyncMock(
            side_effect=execute_foreground
        )
        self.executor.tmux_plugin.get_session_status = AsyncMock(
            side_effect=get_session_status
        )

        foreground_task = asyncio.create_task(
            self.executor.execute_tool(
                {
                    "type": "terminal",
                    "id": "foreground",
                    "command": "build",
                    "timeout": "4s",
                }
            )
        )
        await foreground_started.wait()
        status_task = asyncio.create_task(
            self.executor.execute_tool(
                {
                    "type": "terminal_status",
                    "id": "status",
                    "session_name": "build",
                }
            )
        )

        status_was_available = False
        try:
            await asyncio.wait_for(status_called.wait(), timeout=0.25)
            status_was_available = True
        except asyncio.TimeoutError:
            pass
        finally:
            release_foreground.set()

        foreground_result = await foreground_task
        status_result = await status_task

        self.assertTrue(foreground_result.success, foreground_result.error)
        self.assertTrue(status_result.success, status_result.error)
        self.assertTrue(
            status_was_available,
            "terminal_status waited for unrelated foreground execution",
        )

    async def test_execute_tool_rejects_invalid_terminal_timeout_before_running(self):
        self.executor.shell_executor.run = AsyncMock(
            return_value=ShellResult(success=True, stdout="", stderr="", exit_code=0)
        )

        result = await self.executor.execute_tool(
            {
                "type": "terminal",
                "id": "terminal-invalid-timeout",
                "command": "sleep 60",
                "timeout": "thirty seconds",
            }
        )

        self.executor.shell_executor.run.assert_not_awaited()
        self.assertFalse(result.success)
        self.assertIn("timeout", result.error.lower())

    async def test_execute_mcp_tool_success(self):
        """Test successful MCP tool execution."""
        tool_data = {
            "type": "mcp_tool",
            "id": "mcp_tool_0",
            "name": "file_reader",
            "arguments": {"path": "/test/file.txt"},
            "content": "Read file content",
            "raw": '<tool name="file_reader" path="/test/file.txt">Read file content</tool>',
        }

        # Mock MCP integration
        self.mcp_integration.call_mcp_tool = AsyncMock(
            return_value={"content": [{"type": "text", "text": "File content here"}]}
        )

        result = await self.executor._execute_mcp_tool(tool_data)

        self.assertTrue(result.success)
        self.assertEqual(result.tool_type, "mcp_tool")
        self.assertEqual(result.tool_id, "mcp_tool_0")
        self.assertEqual(result.output, "File content here")

        # Verify MCP call
        self.mcp_integration.call_mcp_tool.assert_awaited_once_with(
            "file_reader", {"path": "/test/file.txt"}, timeout=20
        )

    async def test_execute_mcp_tool_failure(self):
        """Test failed MCP tool execution."""
        tool_data = {
            "type": "mcp_tool",
            "id": "mcp_tool_0",
            "name": "bad_tool",
            "arguments": {},
            "content": "",
            "raw": '<tool name="bad_tool"></tool>',
        }

        # Mock MCP integration failure
        self.mcp_integration.call_mcp_tool = AsyncMock(
            return_value={"error": "Tool not found"}
        )

        result = await self.executor._execute_mcp_tool(tool_data)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Tool not found")

    async def test_execute_mcp_tool_is_error_result_is_a_failure(self):
        """MCP flags a tool-level failure with isError inside a normal result."""
        tool_data = {"type": "mcp_tool", "id": "mcp_tool_0", "name": "read_file"}
        self.mcp_integration.call_mcp_tool = AsyncMock(
            return_value={
                "content": [{"type": "text", "text": "403 Path not in workspace"}],
                "isError": True,
            }
        )

        result = await self.executor._execute_mcp_tool(tool_data)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "403 Path not in workspace")

    async def test_execute_all_tools_mixed(self):
        """Test executing mixed terminal and MCP tools."""
        tools = [
            {
                "type": "terminal",
                "id": "terminal_0",
                "command": "ls",
                "raw": "<terminal>ls</terminal>",
            },
            {
                "type": "mcp_tool",
                "id": "mcp_tool_0",
                "name": "search",
                "arguments": {"query": "test"},
                "content": "",
                "raw": '<tool name="search" query="test"></tool>',
            },
        ]

        with (
            patch.object(
                self.executor, "_execute_terminal_command", new=AsyncMock()
            ) as mock_terminal,
            patch.object(
                self.executor, "_execute_mcp_tool", new=AsyncMock()
            ) as mock_mcp,
        ):

            # Mock return values
            mock_terminal.return_value = MockToolResult(
                "terminal", "terminal_0", True, "file1.txt\nfile2.txt"
            )
            mock_mcp.return_value = MockToolResult(
                "mcp_tool", "mcp_tool_0", True, "Search results"
            )

            results = await self.executor.execute_all_tools(tools)

            self.assertEqual(len(results), 2)
            self.assertEqual(results[0].tool_type, "terminal")
            self.assertEqual(results[1].tool_type, "mcp_tool")

            mock_terminal.assert_called_once()
            mock_mcp.assert_called_once()

    def test_format_result_for_conversation(self):
        """Test formatting tool results for conversation context."""
        # Terminal result
        terminal_result = MockToolResult(
            "terminal",
            "terminal_0",
            True,
            "total 8\ndrwxr-xr-x 2 user user 4096 Jan 1 12:00 test",
        )

        formatted = self.executor.format_result_for_conversation(terminal_result)

        self.assertIn("[terminal]", formatted)
        self.assertIn("total 8", formatted)

        # MCP tool result
        mcp_result = MockToolResult(
            "mcp_tool", "mcp_tool_0", True, "Found 3 matching files"
        )

        formatted = self.executor.format_result_for_conversation(mcp_result)

        self.assertIn("[mcp_tool]", formatted)
        self.assertIn("Found 3 matching files", formatted)

        # Error result
        error_result = MockToolResult(
            "terminal", "terminal_0", False, None, "Permission denied"
        )

        formatted = self.executor.format_result_for_conversation(error_result)

        self.assertIn("ERROR:", formatted)
        self.assertIn("Permission denied", formatted)

    def test_get_execution_stats(self):
        """Test execution statistics tracking."""
        # Simulate some executions
        self.executor.stats = {
            "total_executions": 10,
            "terminal_executions": 6,
            "mcp_executions": 4,
            "successful_executions": 8,
            "failed_executions": 2,
            "total_execution_time": 15.5,
        }

        stats = self.executor.get_execution_stats()

        self.assertEqual(stats["total_executions"], 10)
        self.assertEqual(stats["terminal_executions"], 6)
        self.assertEqual(stats["mcp_executions"], 4)
        self.assertEqual(stats["successful_executions"], 8)
        self.assertEqual(stats["failed_executions"], 2)
        self.assertEqual(stats["success_rate"], 0.8)  # 8/10
        self.assertEqual(stats["average_time"], 1.55)  # 15.5/10

    async def test_tool_execution_with_cancellation(self):
        """Test tool execution cancellation via event bus."""
        tool_data = {
            "type": "terminal",
            "id": "terminal_0",
            "command": "sleep 10",
            "raw": "<terminal>sleep 10</terminal>",
        }

        self.executor.shell_executor.run = AsyncMock(
            return_value=ShellResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=-1,
                cancelled=True,
                error="Command cancelled by user",
            )
        )

        result = await self.executor._execute_terminal_command(tool_data)

        self.assertFalse(result.success)
        self.assertIn("cancelled", result.error.lower())

    def test_result_summary_formatting(self):
        """Test result summary generation for display."""
        # Test various result types
        results = [
            MockToolResult("terminal", "term1", True, "output1"),
            MockToolResult("mcp_tool", "mcp1", True, "output2"),
            MockToolResult("terminal", "term2", False, None, "error1"),
        ]

        summaries = []
        for result in results:
            if result.success:
                summaries.append(f"✓ {result.tool_type} ({result.tool_id})")
            else:
                summaries.append(
                    f"✗ {result.tool_type} ({result.tool_id}): {result.error}"
                )

        self.assertEqual(len(summaries), 3)
        self.assertIn("✓ terminal (term1)", summaries[0])
        self.assertIn("✓ mcp_tool (mcp1)", summaries[1])
        self.assertIn("✗ terminal (term2): error1", summaries[2])

    async def test_concurrent_tool_execution(self):
        """Test concurrent execution of multiple tools."""
        tools = [
            {"type": "terminal", "id": f"terminal_{i}", "command": f"echo {i}"}
            for i in range(5)
        ]

        with patch.object(
            self.executor, "_execute_terminal_command", new=AsyncMock()
        ) as mock_exec:
            # Mock delayed execution
            async def mock_execution(tool_data):
                await asyncio.sleep(0.1)  # Simulate execution time
                tool_id = tool_data["id"]
                return MockToolResult("terminal", tool_id, True, f"output_{tool_id}")

            mock_exec.side_effect = mock_execution

            start_time = asyncio.get_event_loop().time()
            results = await self.executor.execute_all_tools(tools)
            end_time = asyncio.get_event_loop().time()

            # execute_all_tools currently processes tools sequentially.
            execution_time = end_time - start_time
            self.assertGreaterEqual(execution_time, 0.5)

            self.assertEqual(len(results), 5)
            for i, result in enumerate(results):
                self.assertTrue(result.success)
                self.assertEqual(result.tool_id, f"terminal_{i}")

    async def test_cancel_running_tool_closes_active_mcp_connections(self):
        """ESC cancellation must interrupt MCP stdio waits, not only shells."""
        self.mcp_integration.cancel_active_connections = AsyncMock()

        await self.executor.cancel_running_tool()

        self.mcp_integration.cancel_active_connections.assert_awaited_once_with()

    async def test_mcp_reload_reports_partial_reconnect_as_failure(self):
        """A reload with one failed server must not be reported as success."""
        self.mcp_integration.reload_mcp_servers = AsyncMock(
            return_value={
                "configured": 2,
                "discovered": 2,
                "reconnected": 1,
                "failed": 1,
                "failed_servers": ["zai-mcp-server"],
                "cancelled": False,
            }
        )
        self.mcp_integration.get_tool_definitions_for_api = MagicMock(
            return_value=[{"name": "existing_tool"}]
        )

        result = await self.executor._execute_mcp_reload(
            {"type": "mcp_reload", "id": "reload-0"}
        )

        self.assertFalse(result.success)
        self.assertIn("reload incomplete", result.output)
        self.assertIn("zai-mcp-server", result.error)
        self.assertEqual(result.metadata["failed"], 1)


class TestToolExecutorMCPTimeoutOwnership(unittest.IsolatedAsyncioTestCase):
    """MCP integration owns MCP call timeout behavior."""

    async def test_mcp_timeout_is_forwarded_without_outer_cancellation(self):
        class TimeoutOwningMCP:
            def __init__(self):
                self.timeout = None

            async def call_mcp_tool(self, tool_name, arguments, timeout=None):
                self.timeout = timeout
                await asyncio.sleep(0.02)
                return {"error": f"MCP request timed out after {timeout} seconds"}

        mcp = TimeoutOwningMCP()
        executor = ToolExecutor(
            mcp_integration=mcp,
            event_bus=MagicMock(),
            terminal_timeout=10,
            mcp_timeout=0.01,
        )

        result = await executor._execute_mcp_tool(
            {
                "type": "mcp_tool",
                "id": "mcp_tool_0",
                "name": "slow_tool",
                "arguments": {},
            }
        )

        self.assertEqual(mcp.timeout, 0.01)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "MCP request timed out after 0.01 seconds")


if __name__ == "__main__":
    unittest.main(verbosity=2)
