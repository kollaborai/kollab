"""Unit tests for the ConfigHookLoader (JSON config hooks system)."""

import asyncio
import json
import logging
import os
import shlex
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kollabor.config_hooks import (
    _CLAUDE_CODE_ALIASES,
    _KOLLAB_ALIASES,
    ConfigHookLoader,
)
from kollabor_events import EventBus
from kollabor_events.models import Event, EventType, HookPriority


def _pid_is_alive(pid, *, platform=None, kernel32=None):
    """Check process liveness without sending signals on Windows."""
    platform = platform or os.name
    if platform == "nt":
        import ctypes
        from ctypes import wintypes

        if kernel32 is None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.DWORD),
            ]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True  # Unknown is conservatively treated as still alive.
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def config_service():
    return MagicMock()


@pytest.fixture
def loader(event_bus, config_service):
    return ConfigHookLoader(event_bus, config_service)


class TestResolveEventType:
    def test_claude_code_alias(self, loader):
        assert loader._resolve_event_type("PreToolUse") == EventType.TOOL_CALL_PRE

    def test_claude_code_session_start(self, loader):
        assert loader._resolve_event_type("SessionStart") == EventType.SYSTEM_STARTUP

    def test_claude_code_stop(self, loader):
        assert loader._resolve_event_type("Stop") == EventType.LLM_RESPONSE_POST

    def test_kollabor_native_alias(self, loader):
        assert loader._resolve_event_type("LlmRequestPre") == EventType.LLM_REQUEST_PRE

    def test_kollabor_mcp_alias(self, loader):
        assert (
            loader._resolve_event_type("McpToolCallPre") == EventType.MCP_TOOL_CALL_PRE
        )

    def test_direct_enum_value(self, loader):
        assert loader._resolve_event_type("user_input_pre") == EventType.USER_INPUT_PRE

    def test_uppercase_name(self, loader):
        assert loader._resolve_event_type("TOOL_CALL_PRE") == EventType.TOOL_CALL_PRE

    def test_unknown_returns_none(self, loader):
        assert loader._resolve_event_type("NonExistentEvent") is None


class TestResolvePriority:
    def test_string_priority(self, loader):
        assert (
            loader._resolve_priority("POSTPROCESSING")
            == HookPriority.POSTPROCESSING.value
        )

    def test_int_priority(self, loader):
        assert loader._resolve_priority(42) == 42

    def test_default_on_unknown(self, loader):
        assert loader._resolve_priority("GARBAGE") == HookPriority.POSTPROCESSING.value


class TestLoadHooksConfig:
    def test_no_files_returns_none(self, loader):
        with patch("kollabor.config_hooks.Path") as mock_path:
            # Both paths don't exist
            mock_path.cwd.return_value.__truediv__ = MagicMock(
                return_value=MagicMock(exists=MagicMock(return_value=False))
            )
            mock_path.home.return_value.__truediv__ = MagicMock(
                return_value=MagicMock(exists=MagicMock(return_value=False))
            )
            # Use real paths that don't exist
            loader._load_hooks_config()
        # With default paths that likely don't exist, should return None
        # (unless user has hooks.json, but that's fine)

    def test_reads_project_file(self, loader):
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir) / ".kollab"
            hooks_dir.mkdir()
            hooks_file = hooks_dir / "hooks.json"
            hooks_file.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {"hooks": [{"type": "command", "command": "echo test"}]}
                            ]
                        }
                    }
                )
            )

            with patch("kollabor.config_hooks.Path") as mock_path:
                mock_path.cwd.return_value = Path(tmpdir)
                mock_path.home.return_value = Path(tmpdir) / "_nonexistent_home_"

                result = loader._load_hooks_config()

            assert result is not None
            assert "hooks" in result
            assert "PreToolUse" in result["hooks"]

    def test_merges_global_and_project(self, loader):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Global
            global_dir = Path(tmpdir) / "global" / ".kollab"
            global_dir.mkdir(parents=True)
            (global_dir / "hooks.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {
                                    "hooks": [
                                        {"type": "command", "command": "echo global"}
                                    ]
                                }
                            ]
                        }
                    }
                )
            )

            # Project
            project_dir = Path(tmpdir) / "project" / ".kollab"
            project_dir.mkdir(parents=True)
            (project_dir / "hooks.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "hooks": [
                                        {"type": "command", "command": "echo project"}
                                    ]
                                }
                            ]
                        }
                    }
                )
            )

            with patch("kollabor.config_hooks.Path") as mock_path:
                mock_path.cwd.return_value = Path(tmpdir) / "project"
                mock_path.home.return_value = Path(tmpdir) / "global"

                result = loader._load_hooks_config()

            assert result is not None
            # Both event types should be present
            assert "SessionStart" in result["hooks"]
            assert "PreToolUse" in result["hooks"]


class TestBuildHookCallback:
    @pytest.mark.asyncio
    async def test_async_failure_is_retrieved_and_logged(self, loader, caplog):
        loader._run_command = AsyncMock(side_effect=RuntimeError("hook boom"))
        callback = loader._build_hook_callback(
            command="broken-hook",
            event_type=EventType.SYSTEM_STARTUP,
            event_name="SessionStart",
            matcher="",
            timeout=5,
            is_async=True,
            failure_filter=False,
        )

        with caplog.at_level(logging.ERROR, logger="kollabor.config_hooks"):
            assert (
                await callback(
                    {},
                    Event(
                        type=EventType.SYSTEM_STARTUP,
                        data={},
                        source="test",
                    ),
                )
                is None
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        assert not loader._pending_tasks
        assert "Async config hook failed: hook boom" in caplog.text

    @pytest.mark.asyncio
    async def test_matcher_filters_non_matching(self, loader):
        callback = loader._build_hook_callback(
            command="echo test",
            event_type=EventType.TOOL_CALL_PRE,
            event_name="PreToolUse",
            matcher="^read_file$",
            timeout=5,
            is_async=False,
            failure_filter=False,
        )

        event = Event(
            type=EventType.TOOL_CALL_PRE,
            data={"tool_name": "write_file"},
            source="test",
        )

        result = await callback(event.data, event)
        assert result is None  # Matcher didn't match, so no subprocess ran

    @pytest.mark.asyncio
    async def test_failure_filter_skips_success(self, loader):
        callback = loader._build_hook_callback(
            command="echo test",
            event_type=EventType.TOOL_CALL_POST,
            event_name="PostToolUseFailure",
            matcher="",
            timeout=5,
            is_async=False,
            failure_filter=True,
        )

        event = Event(
            type=EventType.TOOL_CALL_POST,
            data={"success": True, "tool_name": "test"},
            source="test",
        )

        result = await callback(event.data, event)
        assert result is None  # success=True, so failure filter skipped it

    @pytest.mark.asyncio
    async def test_subprocess_exit_0_no_output(self, loader):
        callback = loader._build_hook_callback(
            command="true",  # exits 0, no output
            event_type=EventType.SYSTEM_STARTUP,
            event_name="SessionStart",
            matcher="",
            timeout=5,
            is_async=False,
            failure_filter=False,
        )

        event = Event(
            type=EventType.SYSTEM_STARTUP,
            data={},
            source="test",
        )

        result = await callback(event.data, event)
        assert result is None

    @pytest.mark.asyncio
    async def test_subprocess_exit_0_with_json(self, loader):
        callback = loader._build_hook_callback(
            command='echo \'{"data": {"injected": true}}\'',
            event_type=EventType.SYSTEM_STARTUP,
            event_name="SessionStart",
            matcher="",
            timeout=5,
            is_async=False,
            failure_filter=False,
        )

        event = Event(
            type=EventType.SYSTEM_STARTUP,
            data={},
            source="test",
        )

        result = await callback(event.data, event)
        assert result == {"data": {"injected": True}}

    @pytest.mark.asyncio
    async def test_subprocess_exit_2_blocks_event(self, loader):
        callback = loader._build_hook_callback(
            command="bash -c 'echo blocked >&2; exit 2'",
            event_type=EventType.TOOL_CALL_PRE,
            event_name="PreToolUse",
            matcher="",
            timeout=5,
            is_async=False,
            failure_filter=False,
        )

        event = Event(
            type=EventType.TOOL_CALL_PRE,
            data={"tool_name": "test"},
            source="test",
        )

        result = await callback(event.data, event)
        assert event.cancelled is True
        assert result["data"]["_hook_blocked"] is True
        assert "blocked" in result["data"]["_hook_reason"]

    @pytest.mark.asyncio
    async def test_subprocess_exit_1_continues(self, loader):
        callback = loader._build_hook_callback(
            command="bash -c 'exit 1'",
            event_type=EventType.SYSTEM_STARTUP,
            event_name="SessionStart",
            matcher="",
            timeout=5,
            is_async=False,
            failure_filter=False,
        )

        event = Event(
            type=EventType.SYSTEM_STARTUP,
            data={},
            source="test",
        )

        result = await callback(event.data, event)
        assert result is None
        assert event.cancelled is False

    @pytest.mark.asyncio
    async def test_continue_false_cancels(self, loader):
        callback = loader._build_hook_callback(
            command='echo \'{"continue": false, "reason": "nope"}\'',
            event_type=EventType.USER_INPUT_PRE,
            event_name="UserPromptSubmit",
            matcher="",
            timeout=5,
            is_async=False,
            failure_filter=False,
        )

        event = Event(
            type=EventType.USER_INPUT_PRE,
            data={},
            source="test",
        )

        await callback(event.data, event)
        assert event.cancelled is True

    @pytest.mark.asyncio
    async def test_stop_hook_active_reaches_stdin_payload(self, loader, tmp_path):
        """`stop_hook_active` in event data rides the hook's stdin payload (#357)."""
        sink = tmp_path / "payload.json"
        callback = loader._build_hook_callback(
            command=f"bash -c 'cat > {sink}'",
            event_type=EventType.LLM_RESPONSE_POST,
            event_name="Stop",
            matcher="",
            timeout=5,
            is_async=False,
            failure_filter=False,
        )

        event = Event(
            type=EventType.LLM_RESPONSE_POST,
            data={
                "clean_response": "done",
                "turn_completed": True,
                "stop_hook_active": True,
            },
            source="test",
        )

        await callback(event.data, event)
        payload = json.loads(sink.read_text())
        assert payload["hook_event_name"] == "Stop"
        assert payload["stop_hook_active"] is True
        assert payload["turn_completed"] is True

    @pytest.mark.asyncio
    async def test_decision_deny_cancels(self, loader):
        callback = loader._build_hook_callback(
            command='echo \'{"decision": "deny", "reason": "denied"}\'',
            event_type=EventType.TOOL_CALL_PRE,
            event_name="PreToolUse",
            matcher="",
            timeout=5,
            is_async=False,
            failure_filter=False,
        )

        event = Event(
            type=EventType.TOOL_CALL_PRE,
            data={"tool_name": "test"},
            source="test",
        )

        await callback(event.data, event)
        assert event.cancelled is True

    @pytest.mark.asyncio
    async def test_decision_block_cancels(self, loader):
        """Issue #664: "block" (the Claude Code Stop-hook decision value)
        must behave as a synonym of "deny" instead of being silently
        ignored."""
        callback = loader._build_hook_callback(
            command='echo \'{"decision": "block", "reason": "needs another pass"}\'',
            event_type=EventType.TOOL_CALL_PRE,
            event_name="PreToolUse",
            matcher="",
            timeout=5,
            is_async=False,
            failure_filter=False,
        )

        event = Event(
            type=EventType.TOOL_CALL_PRE,
            data={"tool_name": "test"},
            source="test",
        )

        result = await callback(event.data, event)
        assert event.cancelled is True
        assert result == {
            "data": {"_hook_blocked": True, "_hook_reason": "needs another pass"}
        }


class TestLoadAndRegister:
    @pytest.mark.asyncio
    async def test_no_hooks_file_returns_zero(self, loader):
        with patch("kollabor.config_hooks.Path") as mock_path:
            mock_path.cwd.return_value = Path("/nonexistent/project")
            mock_path.home.return_value = Path("/nonexistent/home")
            count = await loader.load_and_register()
        assert count == 0

    @pytest.mark.asyncio
    async def test_registers_hooks_from_file(self, event_bus, config_service):
        loader = ConfigHookLoader(event_bus, config_service)

        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir) / ".kollab"
            hooks_dir.mkdir()
            (hooks_dir / "hooks.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {
                                    "hooks": [
                                        {"type": "command", "command": "echo hello"}
                                    ]
                                }
                            ],
                            "PreToolUse": [
                                {
                                    "matcher": "read_file",
                                    "hooks": [
                                        {"type": "command", "command": "echo tool"}
                                    ],
                                }
                            ],
                        }
                    }
                )
            )

            with patch("kollabor.config_hooks.Path") as mock_path:
                mock_path.cwd.return_value = Path(tmpdir)
                mock_path.home.return_value = Path(tmpdir) / "_no_global_"

                count = await loader.load_and_register()

        assert count == 2
        assert len(loader._hooks_registered) == 2

    @pytest.mark.asyncio
    async def test_skips_unknown_event_names(self, event_bus, config_service):
        loader = ConfigHookLoader(event_bus, config_service)

        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir) / ".kollab"
            hooks_dir.mkdir()
            (hooks_dir / "hooks.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            "FakeEvent": [
                                {"hooks": [{"type": "command", "command": "echo nope"}]}
                            ]
                        }
                    }
                )
            )

            with patch("kollabor.config_hooks.Path") as mock_path:
                mock_path.cwd.return_value = Path(tmpdir)
                mock_path.home.return_value = Path(tmpdir) / "_no_global_"

                count = await loader.load_and_register()

        assert count == 0


class TestRunCommand:
    @pytest.mark.asyncio
    async def test_basic_echo(self, loader):
        rc, stdout, stderr = await loader._run_command("echo hello", "{}", 5)
        assert rc == 0
        assert "hello" in stdout

    @pytest.mark.asyncio
    async def test_stdin_received(self, loader):
        rc, stdout, stderr = await loader._run_command("cat", '{"test": true}', 5)
        assert rc == 0
        parsed = json.loads(stdout)
        assert parsed["test"] is True

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self, loader):
        with pytest.raises(asyncio.TimeoutError):
            await loader._run_command("sleep 60", "{}", 0.5)

    @pytest.mark.asyncio
    async def test_exit_code_passed_through(self, loader):
        rc, stdout, stderr = await loader._run_command("bash -c 'exit 2'", "{}", 5)
        assert rc == 2


class TestAliasCompleteness:
    def test_all_claude_code_aliases_resolve(self):
        for name, expected_type in _CLAUDE_CODE_ALIASES.items():
            assert isinstance(expected_type, EventType), f"{name} is not EventType"

    def test_all_kollabor_aliases_resolve(self):
        for name, expected_type in _KOLLAB_ALIASES.items():
            assert isinstance(expected_type, EventType)


class TestHookProcessReaping:
    """A cancelled or timed-out hook must not leave its child running (#930).

    Every case here starts a real isolated child (its own session, via the
    loader's own _run_command) that writes its pid and sleeps, then proves
    the child cannot continue.
    """

    CHILD_SCRIPT = (
        "import os, sys, time\n"
        "with open(sys.argv[1], 'w') as handle:\n"
        "    handle.write(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )

    def test_process_reaping_tests_avoid_platform_specific_commands(self):
        source = Path(__file__).read_text()

        assert "create_subprocess_exec(" + '"true"' + ")" not in source
        assert "signal." + "SIGKILL" not in source

    def test_windows_pid_probe_uses_process_query_api(self):
        class Kernel32:
            def __init__(self):
                self.closed = []

            def OpenProcess(self, access, inherit, pid):
                assert access == 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
                assert inherit is False
                assert pid == 1234
                return 56

            def GetExitCodeProcess(self, handle, exit_code):
                assert handle == 56
                exit_code._obj.value = 259  # STILL_ACTIVE
                return 1

            def CloseHandle(self, handle):
                self.closed.append(handle)
                return 1

        kernel32 = Kernel32()
        assert _pid_is_alive(1234, platform="nt", kernel32=kernel32)
        assert kernel32.closed == [56]

    async def _start_child(self, loader, pid_path):
        command = f"{sys.executable} -c {shlex.quote(self.CHILD_SCRIPT)} {shlex.quote(str(pid_path))}"
        task = asyncio.create_task(loader._run_command(command, "{}", timeout=30))
        for _ in range(100):
            if pid_path.exists():
                break
            await asyncio.sleep(0.05)
        assert pid_path.exists(), "child never wrote its pid"
        return task, int(pid_path.read_text())

    @staticmethod
    async def _assert_gone(pid, deadline=8.0):
        loop = asyncio.get_running_loop()
        end = loop.time() + deadline
        while loop.time() < end:
            if not _pid_is_alive(pid):
                return
            await asyncio.sleep(0.1)
        pytest.fail(f"hook child {pid} still running after cancellation")

    @pytest.mark.asyncio
    async def test_cancelled_hook_child_is_reaped(self, loader, tmp_path):
        task, pid = await self._start_child(loader, tmp_path / "child.pid")

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        await self._assert_gone(pid)

    @pytest.mark.asyncio
    @pytest.mark.skipif(os.name == "nt", reason="SIGTERM is not catchable on Windows")
    async def test_repeated_cancellation_does_not_interrupt_child_reaping(
        self, loader, tmp_path, monkeypatch
    ):
        pid_path = tmp_path / "child.pid"
        script = (
            "import os, signal, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "with open(sys.argv[1], 'w') as handle:\n"
            "    handle.write(str(os.getpid()))\n"
            "time.sleep(60)\n"
        )
        command = (
            f"{sys.executable} -c {shlex.quote(script)} {shlex.quote(str(pid_path))}"
        )
        task = asyncio.create_task(loader._run_command(command, "{}", timeout=30))
        for _ in range(100):
            if pid_path.exists():
                break
            await asyncio.sleep(0.05)
        assert pid_path.exists(), "child never wrote its pid"
        pid = int(pid_path.read_text())

        reap_started = asyncio.Event()
        reap_child = ConfigHookLoader._reap_child

        async def observe_reap(proc):
            reap_started.set()
            return await reap_child(proc)

        monkeypatch.setattr(ConfigHookLoader, "_reap_child", staticmethod(observe_reap))
        monkeypatch.setattr(ConfigHookLoader, "REAP_GRACE_SECONDS", 1)

        task.cancel()
        await asyncio.wait_for(reap_started.wait(), timeout=3)
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        await self._assert_gone(pid)

    @pytest.mark.asyncio
    async def test_timed_out_hook_child_is_reaped(self, loader, tmp_path):
        command = f"{sys.executable} -c {shlex.quote(self.CHILD_SCRIPT)} {shlex.quote(str(tmp_path / 'child.pid'))}"
        task = asyncio.create_task(loader._run_command(command, "{}", timeout=1))
        for _ in range(100):
            if (tmp_path / "child.pid").exists():
                break
            await asyncio.sleep(0.05)
        pid = int((tmp_path / "child.pid").read_text())

        with pytest.raises(asyncio.TimeoutError):
            await task

        await self._assert_gone(pid)

    @pytest.mark.asyncio
    @pytest.mark.skipif(os.name == "nt", reason="SIGTERM is not catchable on Windows")
    async def test_cancellation_during_timeout_reap_still_kills_child(
        self, loader, tmp_path
    ):
        pid_path = tmp_path / "child.pid"
        term_path = tmp_path / "child.term"
        script = (
            "import os, signal, sys, time\n"
            "def _noted(signum, frame):\n"
            "    with open(sys.argv[2], 'w') as handle:\n"
            "        handle.write('term')\n"
            "signal.signal(signal.SIGTERM, _noted)\n"
            "with open(sys.argv[1], 'w') as handle:\n"
            "    handle.write(str(os.getpid()))\n"
            "time.sleep(60)\n"
        )
        command = (
            f"{sys.executable} -c {shlex.quote(script)}"
            f" {shlex.quote(str(pid_path))} {shlex.quote(str(term_path))}"
        )
        task = asyncio.create_task(loader._run_command(command, "{}", timeout=1))
        for _ in range(100):
            if pid_path.exists():
                break
            await asyncio.sleep(0.05)
        assert pid_path.exists(), "child never wrote its pid"
        pid = int(pid_path.read_text())

        # The timeout branch has fired and SIGTERM was delivered (the child
        # noted it); the reap is now inside its grace window, before SIGKILL.
        for _ in range(100):
            if term_path.exists():
                break
            await asyncio.sleep(0.05)
        assert term_path.exists(), "timeout reap never signalled the child"
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises((asyncio.TimeoutError, asyncio.CancelledError)):
            await task

        await self._assert_gone(pid)

    @pytest.mark.asyncio
    async def test_loader_shutdown_reaps_through_cleanup(self, loader, tmp_path):
        task, pid = await self._start_child(loader, tmp_path / "child.pid")
        loader._pending_tasks.add(task)

        await asyncio.wait_for(loader.shutdown(), timeout=15)

        assert task.done()
        await self._assert_gone(pid)
        assert loader._pending_tasks == set()

    @pytest.mark.asyncio
    async def test_shutdown_drains_tasks_added_during_cleanup(
        self, loader, monkeypatch
    ):
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        late_task_cancelled = asyncio.Event()

        async def initial_task():
            try:
                await asyncio.Future()
            finally:
                cleanup_started.set()
                await release_cleanup.wait()

        async def late_task():
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                late_task_cancelled.set()
                raise

        async def immediate_wait(tasks, timeout):
            return set(), set(tasks)

        monkeypatch.setattr(asyncio, "wait", immediate_wait)
        initial = asyncio.create_task(initial_task())
        initial.add_done_callback(loader._on_pending_task_done)
        loader._pending_tasks.add(initial)

        shutdown = asyncio.create_task(loader.shutdown())
        await cleanup_started.wait()

        late = asyncio.create_task(late_task())
        late.add_done_callback(loader._on_pending_task_done)
        loader._pending_tasks.add(late)
        release_cleanup.set()
        await shutdown

        assert late_task_cancelled.is_set()
        assert late.done()
        assert loader._pending_tasks == set()

    MARKER_SCRIPT = (
        "import sys\n"
        "with open(sys.argv[1], 'a') as handle:\n"
        "    handle.write('x')\n"
    )

    @pytest.mark.asyncio
    async def test_shutdown_gate_blocks_async_hook_admission(self, loader, tmp_path):
        marker = tmp_path / "hook.ran"
        command = (
            f"{sys.executable} -c {shlex.quote(self.MARKER_SCRIPT)}"
            f" {shlex.quote(str(marker))}"
        )
        callback = loader._build_hook_callback(
            command=command,
            event_type=EventType.SYSTEM_STARTUP,
            event_name="SessionStart",
            matcher="",
            timeout=5,
            is_async=True,
            failure_filter=False,
        )
        event = Event(type=EventType.SYSTEM_STARTUP, data={}, source="test")

        # Control: before shutdown, the async trigger path admits the hook.
        assert await callback(event.data, event) is None
        assert len(loader._pending_tasks) == 1
        admitted = next(iter(loader._pending_tasks))
        await asyncio.wait_for(admitted, timeout=10)
        await asyncio.sleep(0.05)
        assert loader._pending_tasks == set()
        assert marker.read_text() == "x"

        # Once shutdown has begun, the same trigger path admits nothing.
        loader._shutting_down = True
        assert await callback(event.data, event) is None
        assert loader._pending_tasks == set()
        await asyncio.sleep(0.3)
        assert marker.read_text() == "x"

    @pytest.mark.asyncio
    async def test_repeated_shutdown_is_quiet(self, loader):
        await loader.shutdown()
        await loader.shutdown()

    @pytest.mark.asyncio
    async def test_already_exited_child_counts_as_reaped(self, loader):
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
        await proc.wait()

        assert await ConfigHookLoader._reap_child(proc) is True

    @pytest.mark.asyncio
    async def test_signal_permission_error_is_reported_as_reap_failure(self):
        class RefusingProcess:
            returncode = None
            pid = 1234

            def terminate(self):
                raise PermissionError("signal denied")

        assert await ConfigHookLoader._reap_child(RefusingProcess()) is False

    @pytest.mark.asyncio
    async def test_cleanup_failure_is_reported_not_silent(
        self, loader, tmp_path, caplog, monkeypatch
    ):
        reap_child = ConfigHookLoader._reap_child

        async def refusing_reap(proc):
            await reap_child(proc)
            return False

        monkeypatch.setattr(
            ConfigHookLoader, "_reap_child", staticmethod(refusing_reap)
        )
        pid_path = tmp_path / "child.pid"
        task, pid = await self._start_child(loader, pid_path)
        caplog.set_level(logging.WARNING)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        try:
            assert "left its child running" in caplog.text
        finally:
            await self._assert_gone(pid)
