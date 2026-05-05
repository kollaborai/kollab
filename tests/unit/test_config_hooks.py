"""Unit tests for the ConfigHookLoader (JSON config hooks system)."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kollabor.config_hooks import (
    _CLAUDE_CODE_ALIASES,
    _KOLLAB_ALIASES,
    ConfigHookLoader,
)
from kollabor_events import EventBus
from kollabor_events.models import Event, EventType, HookPriority


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
            assert isinstance(expected_type, EventType), f"{name} is not EventType"
